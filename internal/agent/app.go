package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"os"
	stdruntime "runtime"
	"strings"
	"time"

	"github.com/kaishzz/kepagent/internal/api"
	"github.com/kaishzz/kepagent/internal/config"
	"github.com/kaishzz/kepagent/internal/constants"
	"github.com/kaishzz/kepagent/internal/runtime"
	"github.com/kaishzz/kepagent/internal/version"
)

type App struct {
	cfg      *config.Config
	client   *api.Client
	runtime  *runtime.Runtime
	logger   *slog.Logger
	handlers map[string]func(context.Context, map[string]any, *LiveLogger) (map[string]any, bool, error)
}

func New(cfg *config.Config, client *api.Client, rt *runtime.Runtime, logger *slog.Logger) *App {
	app := &App{cfg: cfg, client: client, runtime: rt, logger: logger}
	app.handlers = map[string]func(context.Context, map[string]any, *LiveLogger) (map[string]any, bool, error){
		"agent.ping":            app.handlePing,
		"docker.list_servers":   app.handleListServers,
		"docker.start_server":   app.handleStartServer,
		"docker.stop_server":    app.handleStopServer,
		"docker.restart_server": app.handleRestartServer,
		"docker.remove_server":  app.handleRemoveServer,
		"docker.start_group":    app.handleStartGroup,
		"docker.stop_group":     app.handleStopGroup,
		"docker.restart_group":  app.handleRestartGroup,
		"node.kill_all":         app.handleKillAll,
		"node.rcon_command":     app.handleRCONCommand,
		"node.check_update":     app.handleCheckUpdate,
		"node.check_validate":   app.handleCheckValidate,
		"node.get_local_build":  app.handleGetLocalBuild,
		"node.get_remote_build": app.handleGetRemoteBuild,
		"node.monitor_check":    app.handleMonitorCheck,
		"node.monitor_start":    app.handleMonitorStart,
	}
	return app
}

func (a *App) Run(ctx context.Context) error {
	a.logger.Info("KepAgent starting", "version", version.Version)
	if _, err := a.client.FetchMe(ctx); err != nil {
		return fmt.Errorf("agent bootstrap failed: %w", err)
	}
	a.logger.Info("connected to control plane")

	heartbeatTimer := time.NewTimer(0)
	pollTimer := time.NewTimer(0)
	defer heartbeatTimer.Stop()
	defer pollTimer.Stop()

	for {
		select {
		case <-ctx.Done():
			return nil
		case <-heartbeatTimer.C:
			if err := a.reportRuntimeState(ctx, nil); err != nil {
				a.logger.Warn("heartbeat failed", "error", err)
			}
			heartbeatTimer.Reset(a.cfg.HeartbeatInterval())
		case <-pollTimer.C:
			if err := a.processOneCommand(ctx); err != nil {
				a.logger.Warn("poll cycle failed", "error", err)
			}
			pollTimer.Reset(a.cfg.PollInterval())
		}
	}
}

func (a *App) buildHeartbeatPayload(ctx context.Context) map[string]any {
	servers, err := a.runtime.ListServers(ctx)
	if err != nil {
		a.logger.Warn("server list failed while building heartbeat", "error", err)
		servers = []map[string]any{}
	}
	hostname, _ := os.Hostname()
	return map[string]any{
		"agentVersion": version.Version,
		"hostname":     hostname,
		"platform":     stdruntime.GOOS + " " + stdruntime.GOARCH,
		"capabilities": constants.SupportedCommands,
		"summary":      a.runtime.BuildSummary(servers),
		"stats": map[string]any{
			"goVersion": stdruntime.Version(),
		},
		"servers": servers,
		"metadata": map[string]any{
			"machine":     stdruntime.GOARCH,
			"node":        hostname,
			"groupLabels": a.cfg.GroupLabels,
			"groupOrder":  a.cfg.GroupOrder,
		},
	}
}

func (a *App) reportRuntimeState(ctx context.Context, _ []string) error {
	_, err := a.client.SendHeartbeat(ctx, a.buildHeartbeatPayload(ctx))
	return err
}

func (a *App) processOneCommand(ctx context.Context) error {
	command, err := a.client.ClaimCommand(ctx)
	if err != nil || command == nil {
		return err
	}
	commandID := command.ID
	started, err := a.client.MarkCommandStarted(ctx, commandID)
	if err != nil {
		return err
	}
	if responseStatus(started) == "CANCELLED" {
		a.logger.Info("command was cancelled before execution", "id", commandID)
		return nil
	}

	live := NewLiveLogger(func(logCtx context.Context, logs []api.LogEntry) error {
		_, err := a.client.AppendCommandLogs(logCtx, commandID, logs)
		return err
	})
	a.runtime.SetLogEmitter(func(level string, message string) {
		live.Emit(ctx, level, message)
	})
	a.runtime.SetCancelReader(func(readCtx context.Context) (map[string]any, error) {
		fresh, err := a.client.FetchCommand(readCtx, commandID)
		if err != nil || fresh == nil {
			return nil, err
		}
		return map[string]any{
			"status":        fresh.Status,
			"cancelRequest": fresh.CancelRequest,
		}, nil
	})
	a.runtime.SetStateReporter(func(reportCtx context.Context, keys []string) {
		if err := a.reportRuntimeState(reportCtx, keys); err != nil {
			a.logger.Warn("runtime state report failed", "error", err)
		}
	})
	defer func() {
		a.runtime.SetLogEmitter(nil)
		a.runtime.SetCancelReader(nil)
		a.runtime.SetStateReporter(nil)
	}()

	live.Append(ctx, "Executing command: "+command.CommandType)
	if err := live.Flush(ctx); err != nil {
		a.logger.Warn("initial command log flush failed", "error", err)
	}
	result, ok, execErr := a.executeCommand(ctx, command, live)
	_ = live.Flush(ctx)
	_ = a.reportRuntimeState(ctx, nil)

	if execErr != nil {
		var cancelled runtime.CancelledError
		if asCancelled(execErr, &cancelled) {
			live.Emit(ctx, "warning", cancelled.Error())
			_ = live.Flush(ctx)
			_, err = a.client.FinishCommand(ctx, commandID, false, map[string]any{
				"cancelled": true,
				"force":     cancelled.Force,
				"message":   cancelled.Message,
			}, cancelled.Message, true)
			return err
		}
		live.Emit(ctx, "error", "Command failed: "+execErr.Error())
		_ = live.Flush(ctx)
		_, err = a.client.FinishCommand(ctx, commandID, false, nil, execErr.Error(), false)
		return err
	}

	_, err = a.client.FinishCommand(ctx, commandID, ok, compactFinishResult(command.CommandType, result), "", false)
	return err
}

func (a *App) executeCommand(ctx context.Context, command *api.Command, logs *LiveLogger) (map[string]any, bool, error) {
	handler := a.handlers[command.CommandType]
	if handler == nil {
		return nil, false, fmt.Errorf("unsupported command type: %s", command.CommandType)
	}
	return handler(ctx, command.Payload, logs)
}

func (a *App) handlePing(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	logs.Append(ctx, "Ping command completed")
	return map[string]any{"pong": true, "logs": logs.Messages()}, true, nil
}

func (a *App) handleListServers(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	servers, err := a.runtime.ListServers(ctx)
	if err != nil {
		return nil, false, err
	}
	logs.Append(ctx, "Collected docker server list")
	return map[string]any{"summary": a.runtime.BuildSummary(servers), "servers": servers, "logs": logs.Messages()}, true, nil
}

func (a *App) handleStartServer(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	return a.handleServerAction(ctx, "start", payload, logs)
}

func (a *App) handleStopServer(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	return a.handleServerAction(ctx, "stop", payload, logs)
}

func (a *App) handleRestartServer(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	return a.handleServerAction(ctx, "restart", payload, logs)
}

func (a *App) handleRemoveServer(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	return a.handleServerAction(ctx, "remove", payload, logs)
}

func (a *App) handleServerAction(ctx context.Context, action string, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	keys := stringSlice(payload["serverKeys"])
	var result map[string]any
	var err error
	if len(keys) > 0 {
		switch action {
		case "start":
			result, err = a.runtime.StartServers(ctx, keys)
		case "stop":
			result, err = a.runtime.StopServers(ctx, keys)
		case "restart":
			result, err = a.runtime.RestartServers(ctx, keys)
		case "remove":
			result, err = a.runtime.RemoveServers(ctx, keys)
		}
	} else {
		key := requiredString(payload, "key")
		switch action {
		case "start":
			result, err = a.runtime.StartServer(ctx, key)
		case "stop":
			result, err = a.runtime.StopServer(ctx, key)
		case "restart":
			result, err = a.runtime.RestartServer(ctx, key)
		case "remove":
			result, err = a.runtime.RemoveServer(ctx, key)
		}
	}
	if err != nil {
		return nil, false, err
	}
	logs.Append(ctx, fmt.Sprint(result["message"]))
	result["logs"] = logs.Messages()
	return result, true, nil
}

func (a *App) handleStartGroup(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.StartGroup(ctx, requiredString(payload, "group"))
	return finishLogged(ctx, result, true, err, logs)
}

func (a *App) handleStopGroup(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.StopGroup(ctx, requiredString(payload, "group"))
	return finishLogged(ctx, result, true, err, logs)
}

func (a *App) handleRestartGroup(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.RestartGroup(ctx, requiredString(payload, "group"))
	return finishLogged(ctx, result, true, err, logs)
}

func (a *App) handleKillAll(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.RemoveAll(ctx)
	return finishLogged(ctx, result, true, err, logs)
}

func (a *App) handleRCONCommand(ctx context.Context, payload map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	command := strings.TrimSpace(fmt.Sprint(payload["command"]))
	if command == "" {
		return nil, false, fmt.Errorf("RCON command cannot be empty")
	}
	result, err := a.runtime.SendRCONCommand(ctx, firstNonEmpty(fmt.Sprint(payload["group"]), "ALL"), command, stringSlice(payload["serverKeys"]), mapSlice(payload["targets"]))
	if err != nil {
		return nil, false, err
	}
	logs.Append(ctx, fmt.Sprint(result["message"]))
	result["logs"] = logs.Messages()
	return result, truthy(result["ok"]), nil
}

func (a *App) handleCheckUpdate(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.CheckUpdate(ctx)
	return finishLogged(ctx, result, truthy(result["ok"]) || result["ok"] == nil, err, logs)
}

func (a *App) handleCheckValidate(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.CheckValidate(ctx)
	return finishLogged(ctx, result, true, err, logs)
}

func (a *App) handleGetLocalBuild(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.GetLocalBuild(ctx)
	return finishLogged(ctx, result, true, err, logs)
}

func (a *App) handleGetRemoteBuild(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.GetRemoteBuild(ctx)
	return finishLogged(ctx, result, true, err, logs)
}

func (a *App) handleMonitorCheck(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.MonitorCheck(ctx, false)
	return finishLogged(ctx, result, truthy(result["ok"]), err, logs)
}

func (a *App) handleMonitorStart(ctx context.Context, _ map[string]any, logs *LiveLogger) (map[string]any, bool, error) {
	result, err := a.runtime.MonitorCheck(ctx, true)
	return finishLogged(ctx, result, truthy(result["ok"]), err, logs)
}

func finishLogged(ctx context.Context, result map[string]any, ok bool, err error, logs *LiveLogger) (map[string]any, bool, error) {
	if err != nil {
		return nil, false, err
	}
	logs.Append(ctx, fmt.Sprint(result["message"]))
	result["logs"] = logs.Messages()
	return result, ok, nil
}

func requiredString(payload map[string]any, key string) string {
	value := strings.TrimSpace(fmt.Sprint(payload[key]))
	if value == "" || value == "<nil>" {
		return ""
	}
	return value
}

func stringSlice(value any) []string {
	values, ok := value.([]any)
	if !ok {
		if typed, ok := value.([]string); ok {
			return typed
		}
		return nil
	}
	out := []string{}
	for _, item := range values {
		text := strings.TrimSpace(fmt.Sprint(item))
		if text != "" {
			out = append(out, text)
		}
	}
	return out
}

func mapSlice(value any) []map[string]any {
	values, ok := value.([]any)
	if !ok {
		return nil
	}
	out := []map[string]any{}
	for _, item := range values {
		if typed, ok := item.(map[string]any); ok {
			out = append(out, typed)
		}
	}
	return out
}

func compactFinishResult(commandType string, result map[string]any) any {
	content, err := json.Marshal(result)
	if err == nil && len(content) <= 8*1024 {
		return result
	}
	compact := map[string]any{
		"commandType": commandType,
		"message":     result["message"],
		"truncated":   true,
	}
	for _, key := range []string{"validated", "updated", "needsUpdate", "previousBuildId", "currentBuildId", "latestBuildId", "monitorServerKey", "scope", "action", "changed", "total", "serverKeys"} {
		if value, ok := result[key]; ok {
			compact[key] = value
		}
	}
	return compact
}

func responseStatus(response map[string]any) string {
	status := strings.TrimSpace(fmt.Sprint(response["status"]))
	if status == "" || status == "<nil>" {
		if command, ok := response["command"].(map[string]any); ok {
			status = strings.TrimSpace(fmt.Sprint(command["status"]))
		}
	}
	return strings.ToUpper(status)
}

func asCancelled(err error, out *runtime.CancelledError) bool {
	if value, ok := err.(runtime.CancelledError); ok {
		*out = value
		return true
	}
	return false
}

func truthy(value any) bool {
	switch typed := value.(type) {
	case bool:
		return typed
	case nil:
		return false
	default:
		return fmt.Sprint(typed) == "true"
	}
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" && value != "<nil>" {
			return value
		}
	}
	return ""
}
