package runtime

import (
	"bufio"
	"bytes"
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/kaishzz/kepagent/internal/config"
	"github.com/kaishzz/kepagent/internal/dockerapi"
	"github.com/kaishzz/kepagent/internal/query"
	"github.com/kaishzz/kepagent/internal/rcon"
)

type CancelledError struct {
	Message string
	Force   bool
}

func (e CancelledError) Error() string {
	return e.Message
}

type Runtime struct {
	cfg           *config.Config
	docker        *dockerapi.Client
	logger        *slog.Logger
	serversByKey  map[string]config.Server
	groups        map[string][]config.Server
	cancelReader  func(context.Context) (map[string]any, error)
	logEmitter    func(level string, message string)
	stateReporter func(context.Context, []string)
}

const batchStartIntervalSeconds = 15

func New(cfg *config.Config, docker *dockerapi.Client, logger *slog.Logger) *Runtime {
	rt := &Runtime{
		cfg:          cfg,
		docker:       docker,
		logger:       logger,
		serversByKey: map[string]config.Server{},
		groups:       map[string][]config.Server{},
	}
	for _, server := range cfg.Servers {
		rt.serversByKey[server.Key] = server
		for _, group := range server.Groups {
			rt.groups[group] = append(rt.groups[group], server)
		}
	}
	return rt
}

func (r *Runtime) SetCancelReader(reader func(context.Context) (map[string]any, error)) {
	r.cancelReader = reader
}

func (r *Runtime) SetLogEmitter(emitter func(level string, message string)) {
	r.logEmitter = emitter
}

func (r *Runtime) SetStateReporter(reporter func(context.Context, []string)) {
	r.stateReporter = reporter
}

func (r *Runtime) emit(level string, format string, args ...any) {
	message := fmt.Sprintf(format, args...)
	if r.logEmitter != nil {
		r.logEmitter(level, message)
	}
	if r.logger != nil {
		switch level {
		case "error":
			r.logger.Error(message)
		case "warning", "warn":
			r.logger.Warn(message)
		default:
			r.logger.Info(message)
		}
	}
}

func (r *Runtime) checkCancelled(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if r.cancelReader == nil {
		return nil
	}
	command, err := r.cancelReader(ctx)
	if err != nil || command == nil {
		return nil
	}
	status := strings.ToUpper(strings.TrimSpace(fmt.Sprint(command["status"])))
	if status != "CANCELLED" {
		return nil
	}
	payload, _ := command["cancelRequest"].(map[string]any)
	reason := strings.TrimSpace(fmt.Sprint(payload["reason"]))
	force, _ := payload["force"].(bool)
	if reason == "" {
		reason = "Command cancelled"
	}
	return CancelledError{Message: reason, Force: force}
}

func (r *Runtime) server(key string) (config.Server, error) {
	server, ok := r.serversByKey[key]
	if !ok {
		return config.Server{}, fmt.Errorf("unknown server key: %s", key)
	}
	return server, nil
}

func (r *Runtime) serversForKeys(keys []string) ([]config.Server, error) {
	seen := map[string]bool{}
	servers := make([]config.Server, 0, len(keys))
	for _, key := range keys {
		key = strings.TrimSpace(key)
		if key == "" || seen[key] {
			continue
		}
		server, err := r.server(key)
		if err != nil {
			return nil, err
		}
		seen[key] = true
		servers = append(servers, server)
	}
	return servers, nil
}

func (r *Runtime) InspectServer(ctx context.Context, key string) (map[string]any, error) {
	server, err := r.server(key)
	if err != nil {
		return nil, err
	}
	base := r.baseServerPayload(server)
	container, err := r.docker.InspectContainer(ctx, server.ContainerName)
	if err != nil {
		return nil, err
	}
	if container == nil {
		base["state"] = "missing"
		base["status"] = "missing"
		base["containerStatus"] = "missing"
		base["agentA2sStatus"] = "unknown"
		base["image"] = server.Image
		return base, nil
	}

	status := strings.ToLower(strings.TrimSpace(container.State.Status))
	if status == "" {
		status = "unknown"
	}
	restartCount := container.RestartCount
	if container.State.RestartCount > restartCount {
		restartCount = container.State.RestartCount
	}
	base["state"] = status
	base["status"] = status
	base["containerStatus"] = status
	base["agentA2sStatus"] = "unknown"
	base["id"] = container.ID
	base["image"] = firstNonEmpty(container.Config.Image, server.Image, container.Image)
	base["restartCount"] = restartCount

	if status == "running" && r.cfg.ServerQueryEnabled {
		info, err := query.Info(r.cfg.ServerQueryHost, r.serverQueryPort(server), r.cfg.ServerQueryTimeout())
		if err != nil {
			base["agentA2sStatus"] = a2sErrorStatus(err)
			base["agentA2sError"] = err.Error()
			base["queryError"] = err.Error()
		} else {
			for k, v := range info {
				base[k] = v
			}
			base["agentA2sStatus"] = "ok"
			base["agentA2sError"] = nil
		}
	}
	return base, nil
}

func (r *Runtime) ListServers(ctx context.Context) ([]map[string]any, error) {
	rows := make([]map[string]any, 0, len(r.cfg.Servers))
	for _, server := range r.cfg.Servers {
		if err := r.checkCancelled(ctx); err != nil {
			return nil, err
		}
		row, err := r.InspectServer(ctx, server.Key)
		if err != nil {
			row = r.baseServerPayload(server)
			row["state"] = "error"
			row["status"] = "error"
			row["containerStatus"] = "error"
			row["agentA2sStatus"] = "unknown"
			row["error"] = err.Error()
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func (r *Runtime) BuildSummary(servers []map[string]any) map[string]any {
	running := 0
	missing := 0
	for _, server := range servers {
		switch fmt.Sprint(server["state"]) {
		case "running":
			running++
		case "missing":
			missing++
		}
	}
	return map[string]any{
		"configuredServers": len(r.cfg.Servers),
		"runningServers":    running,
		"missingServers":    missing,
	}
}

func (r *Runtime) StartServer(ctx context.Context, key string) (map[string]any, error) {
	server, err := r.server(key)
	if err != nil {
		return nil, err
	}
	if err := r.checkCancelled(ctx); err != nil {
		return nil, err
	}
	container, err := r.docker.InspectContainer(ctx, server.ContainerName)
	if err != nil {
		return nil, err
	}
	if container != nil {
		status := strings.ToLower(strings.TrimSpace(container.State.Status))
		if status == "running" {
			snapshot, _ := r.InspectServer(ctx, key)
			return map[string]any{
				"changed": false,
				"message": fmt.Sprintf("%s already running", server.ContainerName),
				"server":  snapshot,
			}, nil
		}
		if err := r.docker.RemoveContainer(ctx, server.ContainerName, true); err != nil {
			return nil, err
		}
	}

	if _, err := r.docker.CreateContainer(ctx, server.ContainerName, createContainerRequest(server)); err != nil {
		return nil, err
	}
	if err := r.docker.StartContainer(ctx, server.ContainerName); err != nil {
		return nil, err
	}
	snapshot, _ := r.InspectServer(ctx, key)
	return map[string]any{
		"changed": true,
		"message": fmt.Sprintf("%s started", server.ContainerName),
		"server":  snapshot,
	}, nil
}

func (r *Runtime) StopServer(ctx context.Context, key string) (map[string]any, error) {
	return r.removeServer(ctx, key, "force removed")
}

func (r *Runtime) RemoveServer(ctx context.Context, key string) (map[string]any, error) {
	return r.removeServer(ctx, key, "removed")
}

func (r *Runtime) removeServer(ctx context.Context, key string, actionText string) (map[string]any, error) {
	server, err := r.server(key)
	if err != nil {
		return nil, err
	}
	if err := r.checkCancelled(ctx); err != nil {
		return nil, err
	}
	container, err := r.docker.InspectContainer(ctx, server.ContainerName)
	if err != nil {
		return nil, err
	}
	if container == nil {
		snapshot, _ := r.InspectServer(ctx, key)
		return map[string]any{
			"changed": false,
			"message": fmt.Sprintf("%s not found", server.ContainerName),
			"server":  snapshot,
		}, nil
	}
	if err := r.docker.RemoveContainer(ctx, server.ContainerName, true); err != nil {
		return nil, err
	}
	snapshot, _ := r.InspectServer(ctx, key)
	return map[string]any{
		"changed": true,
		"message": fmt.Sprintf("%s %s", server.ContainerName, actionText),
		"server":  snapshot,
	}, nil
}

func (r *Runtime) RestartServer(ctx context.Context, key string) (map[string]any, error) {
	server, err := r.server(key)
	if err != nil {
		return nil, err
	}
	removed := false
	if container, err := r.docker.InspectContainer(ctx, server.ContainerName); err != nil {
		return nil, err
	} else if container != nil {
		if err := r.docker.RemoveContainer(ctx, server.ContainerName, true); err != nil {
			return nil, err
		}
		removed = true
	}
	result, err := r.StartServer(ctx, key)
	if err != nil {
		return nil, err
	}
	result["changed"] = true
	result["removed"] = removed
	result["message"] = fmt.Sprintf("%s recreated", server.ContainerName)
	return result, nil
}

func (r *Runtime) StartServers(ctx context.Context, keys []string) (map[string]any, error) {
	return r.runServers(ctx, "start", keys)
}

func (r *Runtime) StopServers(ctx context.Context, keys []string) (map[string]any, error) {
	return r.runServers(ctx, "stop", keys)
}

func (r *Runtime) RestartServers(ctx context.Context, keys []string) (map[string]any, error) {
	return r.runServers(ctx, "restart", keys)
}

func (r *Runtime) RemoveServers(ctx context.Context, keys []string) (map[string]any, error) {
	return r.runServers(ctx, "remove", keys)
}

func (r *Runtime) StartGroup(ctx context.Context, group string) (map[string]any, error) {
	return r.runGroup(ctx, group, "start")
}

func (r *Runtime) StopGroup(ctx context.Context, group string) (map[string]any, error) {
	return r.runGroup(ctx, group, "stop")
}

func (r *Runtime) RestartGroup(ctx context.Context, group string) (map[string]any, error) {
	return r.runGroup(ctx, group, "restart")
}

func (r *Runtime) RemoveAll(ctx context.Context) (map[string]any, error) {
	return r.runActionList(ctx, "remove", r.cfg.Servers)
}

func (r *Runtime) runServers(ctx context.Context, action string, keys []string) (map[string]any, error) {
	servers, err := r.serversForKeys(keys)
	if err != nil {
		return nil, err
	}
	result, err := r.runActionList(ctx, action, servers)
	if err != nil {
		return nil, err
	}
	result["scope"] = "servers"
	result["action"] = action
	result["serverKeys"] = keysFromServers(servers)
	result["message"] = fmt.Sprintf("Batch %s handled %d servers, changed %d", action, asInt(result["total"]), asInt(result["changed"]))
	return result, nil
}

func (r *Runtime) runGroup(ctx context.Context, group string, action string) (map[string]any, error) {
	servers, ok := r.groups[group]
	if !ok {
		return nil, fmt.Errorf("unknown server group: %s", group)
	}
	result, err := r.runActionList(ctx, action, servers)
	if err != nil {
		return nil, err
	}
	result["group"] = group
	result["action"] = action
	return result, nil
}

func (r *Runtime) runActionList(ctx context.Context, action string, servers []config.Server) (map[string]any, error) {
	if action == "restart" {
		return r.restartActionList(ctx, servers)
	}
	results := make([]map[string]any, 0, len(servers))
	changed := 0
	reported := []string{}
	for i, server := range servers {
		if err := r.checkCancelled(ctx); err != nil {
			return nil, err
		}
		result, err := r.runSingleAction(ctx, action, server.Key)
		if err != nil {
			return nil, err
		}
		results = append(results, result)
		if truthy(result["changed"]) {
			changed++
		}
		reported = appendUnique(reported, server.Key)
		r.reportState(ctx, []string{server.Key})
		if action == "start" {
			r.waitBeforeNextStart(ctx, servers, i, reported)
		}
	}
	return map[string]any{"changed": changed, "total": len(results), "results": results}, nil
}

func (r *Runtime) restartActionList(ctx context.Context, servers []config.Server) (map[string]any, error) {
	removedByKey := map[string]bool{}
	for _, server := range servers {
		if err := r.checkCancelled(ctx); err != nil {
			return nil, err
		}
		container, err := r.docker.InspectContainer(ctx, server.ContainerName)
		if err != nil {
			return nil, err
		}
		if container != nil {
			if err := r.docker.RemoveContainer(ctx, server.ContainerName, true); err != nil {
				return nil, err
			}
			removedByKey[server.Key] = true
		}
	}
	results := make([]map[string]any, 0, len(servers))
	reported := []string{}
	for i, server := range servers {
		result, err := r.StartServer(ctx, server.Key)
		if err != nil {
			return nil, err
		}
		result["changed"] = true
		result["removed"] = removedByKey[server.Key]
		result["message"] = fmt.Sprintf("%s recreated", server.ContainerName)
		results = append(results, result)
		reported = appendUnique(reported, server.Key)
		r.reportState(ctx, []string{server.Key})
		r.waitBeforeNextStart(ctx, servers, i, reported)
	}
	return map[string]any{"changed": len(results), "total": len(results), "results": results}, nil
}

func (r *Runtime) runSingleAction(ctx context.Context, action string, key string) (map[string]any, error) {
	switch action {
	case "start":
		return r.StartServer(ctx, key)
	case "stop":
		return r.StopServer(ctx, key)
	case "remove":
		return r.RemoveServer(ctx, key)
	case "restart":
		return r.RestartServer(ctx, key)
	default:
		return nil, fmt.Errorf("unsupported action %s", action)
	}
}

func (r *Runtime) waitBeforeNextStart(ctx context.Context, servers []config.Server, index int, reported []string) {
	if len(servers) < 2 || index >= len(servers)-1 {
		return
	}
	r.emit("info", "Waiting %d seconds before starting next server", batchStartIntervalSeconds)
	timer := time.NewTimer(time.Duration(batchStartIntervalSeconds) * time.Second)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
		r.reportState(ctx, reported)
	}
}

func (r *Runtime) reportState(ctx context.Context, keys []string) {
	if r.stateReporter != nil {
		r.stateReporter(ctx, keys)
	}
}

func (r *Runtime) SendRCONCommand(ctx context.Context, group string, command string, serverKeys []string, targets []map[string]any) (map[string]any, error) {
	passwordByKey := map[string]string{}
	hostByKey := map[string]string{}
	targetKeys := append([]string{}, serverKeys...)
	for _, target := range targets {
		key := mapStringValue(target, "key")
		if key == "" {
			continue
		}
		if !slices.Contains(targetKeys, key) {
			targetKeys = append(targetKeys, key)
		}
		host := firstNonEmpty(mapStringValue(target, "host"), mapStringValue(target, "ip"))
		if host != "" {
			hostByKey[key] = host
		}
		password := mapStringValue(target, "password")
		if password != "" {
			passwordByKey[key] = password
		}
	}
	if len(targetKeys) == 0 && strings.ToUpper(group) != "ALL" {
		for _, server := range r.groups[group] {
			targetKeys = append(targetKeys, server.Key)
		}
	}
	if len(targetKeys) == 0 {
		for _, server := range r.cfg.Servers {
			targetKeys = append(targetKeys, server.Key)
		}
	}
	servers, err := r.serversForKeys(targetKeys)
	if err != nil {
		return nil, err
	}

	results := make([]map[string]any, 0, len(servers))
	success := 0
	for _, server := range servers {
		if err := r.checkCancelled(ctx); err != nil {
			return nil, err
		}
		port := r.serverPrimaryPort(server)
		host := hostByKey[server.Key]
		password := passwordByKey[server.Key]
		response := ""
		ok := false
		errorMessage := ""
		r.emit("info", "RCON %s %s:%d sending command", server.Key, host, port)
		if strings.TrimSpace(host) == "" {
			errorMessage = "RCON host is empty"
		} else if strings.TrimSpace(password) == "" {
			errorMessage = "RCON password is empty"
		} else {
			response, err = rcon.Run(host, port, password, command, r.cfg.RCONTimeout())
			if err != nil {
				errorMessage = err.Error()
			} else {
				ok = true
				success++
			}
		}
		if ok {
			r.emit("info", "RCON %s %s:%d succeeded", server.Key, host, port)
		} else {
			r.emit("error", "RCON %s %s:%d failed: %s", server.Key, host, port, errorMessage)
		}
		var errorValue any
		if errorMessage != "" {
			errorValue = errorMessage
		}
		results = append(results, map[string]any{
			"key":      server.Key,
			"host":     host,
			"port":     port,
			"ok":       ok,
			"response": response,
			"error":    errorValue,
		})
	}
	return map[string]any{
		"group":   group,
		"command": command,
		"total":   len(results),
		"success": success,
		"failed":  len(results) - success,
		"ok":      len(results) > 0 && success == len(results),
		"results": results,
		"message": fmt.Sprintf("RCON sent to %d servers, success %d, failed %d", len(results), success, len(results)-success),
	}, nil
}

func (r *Runtime) GetLocalBuild(ctx context.Context) (map[string]any, error) {
	if err := r.checkCancelled(ctx); err != nil {
		return nil, err
	}
	buildID, err := r.localBuildID()
	if err != nil {
		return nil, err
	}
	r.emit("info", "Read local manifest buildid %s", buildID)
	return map[string]any{"buildId": buildID, "message": "Current buildid: " + buildID}, nil
}

func (r *Runtime) GetRemoteBuild(ctx context.Context) (map[string]any, error) {
	if err := r.checkCancelled(ctx); err != nil {
		return nil, err
	}
	r.emit("info", "Running steamcmd app_info_print for app %d", r.cfg.AppID)
	out, err := r.runProcess(ctx, 120*time.Second, r.cfg.SteamCMDPath, "+login", "anonymous", "+app_info_print", strconv.Itoa(r.cfg.AppID), "+quit")
	if err != nil {
		return nil, err
	}
	buildID := extractRemoteBuildID(out)
	if buildID == "" {
		return nil, fmt.Errorf("failed to extract remote buildid")
	}
	r.emit("info", "Resolved remote buildid %s", buildID)
	return map[string]any{"buildId": buildID, "message": "Latest buildid: " + buildID}, nil
}

func (r *Runtime) CheckValidate(ctx context.Context) (map[string]any, error) {
	return r.runCheckValidate(ctx, "", false)
}

func (r *Runtime) CheckUpdate(ctx context.Context) (map[string]any, error) {
	localBuild, err := r.localBuildIDOptional()
	if err != nil {
		return nil, err
	}
	if localBuild == "" {
		r.emit("info", "没有 manifest，直接进入 validate 流程")
		validated, err := r.runCheckValidate(ctx, "", true)
		if err != nil {
			return nil, err
		}
		monitor, err := r.MonitorCheck(ctx, true)
		if err != nil {
			return nil, err
		}
		validated["ok"] = truthy(monitor["ok"])
		validated["monitor"] = monitor
		validated["message"] = monitor["message"]
		return validated, nil
	}
	remote, err := r.GetRemoteBuild(ctx)
	if err != nil {
		return nil, err
	}
	remoteBuild := fmt.Sprint(remote["buildId"])
	if localBuild == remoteBuild {
		return map[string]any{
			"currentBuildId": localBuild,
			"latestBuildId":  remoteBuild,
			"needsUpdate":    false,
			"updated":        false,
			"validated":      false,
			"monitor":        nil,
			"message":        "Already latest version, skipped validate and monitor",
		}, nil
	}
	validated, err := r.runCheckValidate(ctx, localBuild, true)
	if err != nil {
		return nil, err
	}
	monitor, err := r.MonitorCheck(ctx, true)
	if err != nil {
		return nil, err
	}
	validated["ok"] = truthy(monitor["ok"])
	validated["latestBuildId"] = remoteBuild
	validated["needsUpdate"] = false
	validated["monitor"] = monitor
	validated["message"] = monitor["message"]
	return validated, nil
}

func (r *Runtime) runCheckValidate(ctx context.Context, beforeBuild string, beforeKnown bool) (map[string]any, error) {
	if !beforeKnown {
		var err error
		beforeBuild, err = r.localBuildIDOptional()
		if err != nil {
			return nil, err
		}
	}
	update, err := r.runAppUpdateValidate(ctx)
	if err != nil {
		return nil, err
	}
	latest, err := r.localBuildIDOptional()
	if err != nil {
		return nil, err
	}
	message := "Validated, but current buildid is unavailable"
	if latest != "" {
		if beforeBuild != "" && beforeBuild != latest {
			message = "Validated and updated to buildid " + latest
		} else {
			message = "Validated current buildid " + latest
		}
	}
	return map[string]any{
		"validated":       true,
		"updated":         beforeBuild != "" && latest != "" && beforeBuild != latest,
		"previousBuildId": nilIfEmpty(beforeBuild),
		"currentBuildId":  nilIfEmpty(latest),
		"latestBuildId":   nilIfEmpty(latest),
		"needsUpdate":     false,
		"message":         message,
		"update":          update,
	}, nil
}

func (r *Runtime) runAppUpdateValidate(ctx context.Context) (map[string]any, error) {
	r.emit("info", "Removing configured containers before steamcmd validate")
	stopAll, err := r.RemoveAll(ctx)
	if err != nil {
		return nil, err
	}
	if err := r.cleanupSteamapps(); err != nil {
		return nil, err
	}
	r.emit("info", "Running steamcmd app_update %d validate", r.cfg.AppID)
	output, err := r.runProcessWithLiveOutputUntil(ctx, time.Hour, r.buildSteamcmdValidateStopCondition(), r.cfg.SteamCMDPath, "+force_install_dir", r.cfg.CS2Root, "+login", "anonymous", "+app_update", strconv.Itoa(r.cfg.AppID), "validate", "+quit")
	if err != nil {
		return nil, err
	}
	r.emit("info", "steamcmd app_update validate completed successfully")
	metamod, err := r.ensureMetamodPath()
	if err != nil {
		return nil, err
	}
	return map[string]any{"stopAll": stopAll, "output": output, "metamod": metamod}, nil
}

func (r *Runtime) MonitorCheck(ctx context.Context, startAfterSuccess bool) (map[string]any, error) {
	if len(r.cfg.MonitorProfiles) > 0 {
		return r.monitorProfilesCheck(ctx, startAfterSuccess)
	}
	return r.monitorCheckSingle(ctx, startAfterSuccess, "", nil)
}

func (r *Runtime) monitorProfilesCheck(ctx context.Context, startAfterSuccess bool) (map[string]any, error) {
	results := []map[string]any{}
	success := 0
	failed := 0
	for _, profile := range r.cfg.MonitorProfiles {
		if err := r.checkCancelled(ctx); err != nil {
			return nil, err
		}
		startKeys := []string{}
		if startAfterSuccess {
			startKeys = r.profileStartKeys(profile)
		}
		result, err := r.monitorCheckSingle(ctx, startAfterSuccess, profile.MonitorServerKey, startKeys)
		if err != nil {
			failed++
			r.emit("error", "Monitor profile %s failed: %s", profile.Key, err)
			results = append(results, map[string]any{
				"ok":               false,
				"profileKey":       profile.Key,
				"monitorServerKey": profile.MonitorServerKey,
				"startServerKeys":  startKeys,
				"message":          err.Error(),
			})
			continue
		}
		success++
		result["profileKey"] = profile.Key
		result["startServerKeys"] = startKeys
		results = append(results, result)
	}
	actionText := "checked"
	if startAfterSuccess {
		actionText = "checked and started configured servers"
	}
	return map[string]any{
		"ok":             failed == 0,
		"profileResults": results,
		"success":        success,
		"failed":         failed,
		"total":          len(results),
		"message":        fmt.Sprintf("Monitor profiles %s: %d succeeded, %d failed", actionText, success, failed),
	}, nil
}

func (r *Runtime) monitorCheckSingle(ctx context.Context, startAfterSuccess bool, monitorServerKey string, startServerKeys []string) (map[string]any, error) {
	monitorKey := strings.TrimSpace(monitorServerKey)
	if monitorKey == "" {
		monitorKey = strings.TrimSpace(r.cfg.MonitorServerKey)
	}
	if monitorKey == "" {
		return nil, fmt.Errorf("monitor_server_key is required")
	}
	server, err := r.server(monitorKey)
	if err != nil {
		return nil, err
	}
	r.emit("info", "Launching monitor server %s using container %s", monitorKey, server.ContainerName)
	launch, err := r.StartServer(ctx, monitorKey)
	if err != nil {
		return nil, err
	}
	container, err := r.docker.InspectContainer(ctx, server.ContainerName)
	if err != nil {
		return nil, err
	}
	if container == nil {
		return nil, fmt.Errorf("monitor container missing after launch: %s", server.ContainerName)
	}
	baseRestart := container.RestartCount
	lastStatus := ""
	runningSince := time.Time{}
	nonRunningSince := time.Now()
	startedAt := time.Now()
	timeline := []map[string]any{}
	ticker := time.NewTicker(time.Duration(max(1, r.cfg.MonitorPollIntervalSeconds)) * time.Second)
	defer ticker.Stop()
	for {
		if err := r.checkCancelled(ctx); err != nil {
			return nil, err
		}
		container, err = r.docker.InspectContainer(ctx, server.ContainerName)
		if err != nil {
			return nil, err
		}
		if container == nil {
			return nil, fmt.Errorf("monitor container missing: %s", server.ContainerName)
		}
		status := strings.ToLower(strings.TrimSpace(container.State.Status))
		restartCount := container.RestartCount
		delta := restartCount - baseRestart
		now := time.Now()
		timeline = append(timeline, map[string]any{"status": status, "restartCount": restartCount, "timestamp": now.Unix()})
		if status != lastStatus {
			r.emit("info", "Monitor %s: status=%s, restartCount=%d", monitorKey, status, restartCount)
		}
		if delta >= r.cfg.MonitorRestartThreshold {
			_, _ = r.RemoveServer(ctx, monitorKey)
			return nil, fmt.Errorf("restart threshold reached for %s: %d", monitorKey, delta)
		}
		if status == "running" {
			if lastStatus != "running" {
				runningSince = now
			}
			if now.Sub(runningSince) >= time.Duration(r.cfg.MonitorStableSeconds)*time.Second {
				_ = r.docker.StopContainer(ctx, server.ContainerName, 10)
				result := map[string]any{
					"ok":               true,
					"monitorServerKey": monitorKey,
					"monitorLaunch":    launch,
					"timeline":         tail(timeline, 50),
					"message":          fmt.Sprintf("Monitor success after %d stable seconds", r.cfg.MonitorStableSeconds),
				}
				result["monitorServer"], _ = r.InspectServer(ctx, monitorKey)
				if startAfterSuccess {
					startResult, err := r.StartAfterMonitor(ctx, monitorKey, startServerKeys)
					if err != nil {
						return nil, err
					}
					result["startServers"] = startResult
					result["message"] = fmt.Sprintf("%s, %s", result["message"], strings.ToLower(fmt.Sprint(startResult["message"])))
				}
				return result, nil
			}
		} else {
			if lastStatus == "running" {
				nonRunningSince = now
			}
			if now.Sub(nonRunningSince) >= time.Duration(r.cfg.MonitorRecoverTimeoutSeconds)*time.Second {
				_, _ = r.RemoveServer(ctx, monitorKey)
				return nil, fmt.Errorf("monitor timeout for %s: status=%s", monitorKey, status)
			}
		}
		if now.Sub(startedAt) > time.Duration(r.cfg.MonitorStableSeconds+r.cfg.MonitorRecoverTimeoutSeconds+3600)*time.Second {
			return nil, fmt.Errorf("monitor exceeded maximum runtime")
		}
		lastStatus = status
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-ticker.C:
		}
	}
}

func (r *Runtime) StartAfterMonitor(ctx context.Context, monitorServerKey string, startServerKeys []string) (map[string]any, error) {
	keys := startServerKeys
	if len(keys) == 0 {
		keys = r.defaultStartKeys(monitorServerKey)
	}
	result, err := r.StartServers(ctx, keys)
	if err != nil {
		return nil, err
	}
	result["message"] = fmt.Sprintf("Started %d servers after monitor success", asInt(result["total"]))
	return result, nil
}

func (r *Runtime) baseServerPayload(server config.Server) map[string]any {
	return map[string]any{
		"key":             server.Key,
		"catalogServerId": nilIfEmpty(server.CatalogServerID),
		"containerName":   server.ContainerName,
		"groups":          server.Groups,
		"primaryPort":     r.serverPrimaryPort(server),
		"host":            nil,
	}
}

func (r *Runtime) serverQueryPort(server config.Server) int {
	return pickPort(server, "udp")
}

func (r *Runtime) serverPrimaryPort(server config.Server) int {
	return pickPort(server, "tcp")
}

func pickPort(server config.Server, preferred string) int {
	for _, port := range server.Ports {
		if strings.EqualFold(port.Protocol, preferred) {
			return port.HostPort
		}
	}
	if len(server.Ports) > 0 {
		return server.Ports[0].HostPort
	}
	return 0
}

func createContainerRequest(server config.Server) dockerapi.CreateContainerRequest {
	env := []string{}
	for key, value := range server.Env {
		env = append(env, key+"="+value)
	}
	exposed := map[string]struct{}{}
	portBindings := map[string][]dockerapi.PortBinding{}
	for _, port := range server.Ports {
		proto := port.Protocol
		if proto == "" {
			proto = "tcp"
		}
		key := fmt.Sprintf("%d/%s", port.ContainerPort, proto)
		exposed[key] = struct{}{}
		portBindings[key] = []dockerapi.PortBinding{{HostPort: strconv.Itoa(port.HostPort)}}
	}
	binds := []string{}
	for _, volume := range server.Volumes {
		binds = append(binds, fmt.Sprintf("%s:%s:%s", volume.HostPath, volume.ContainerPath, firstNonEmpty(volume.Mode, "rw")))
	}
	return dockerapi.CreateContainerRequest{
		Image:        server.Image,
		Cmd:          server.Command,
		Entrypoint:   server.Entrypoint,
		Env:          env,
		Labels:       server.Labels,
		WorkingDir:   server.WorkingDir,
		OpenStdin:    server.StdinOpen,
		Tty:          server.TTY,
		ExposedPorts: exposed,
		HostConfig: dockerapi.HostConfig{
			Binds:         binds,
			PortBindings:  portBindings,
			NetworkMode:   server.NetworkMode,
			RestartPolicy: dockerapi.RestartPolicy{Name: server.RestartPolicy},
		},
	}
}

func (r *Runtime) localBuildID() (string, error) {
	content, err := os.ReadFile(filepath.Join(r.cfg.CS2Root, "steamapps", fmt.Sprintf("appmanifest_%d.acf", r.cfg.AppID)))
	if err != nil {
		return "", err
	}
	match := regexp.MustCompile(`"buildid"\s+"(\d+)"`).FindStringSubmatch(string(content))
	if len(match) < 2 {
		return "", fmt.Errorf("failed to extract local buildid")
	}
	return match[1], nil
}

func (r *Runtime) localBuildIDOptional() (string, error) {
	buildID, err := r.localBuildID()
	if err != nil {
		if os.IsNotExist(err) {
			r.emit("info", "没有 manifest")
			return "", nil
		}
		return "", err
	}
	return buildID, nil
}

func extractRemoteBuildID(output string) string {
	match := regexp.MustCompile(`(?s)"branches"\s*:?\s*\{.*?"public"\s*:?\s*\{.*?"buildid"\s*:?\s*"([^"]+)"`).FindStringSubmatch(output)
	if len(match) < 2 {
		return ""
	}
	return match[1]
}

func (r *Runtime) cleanupSteamapps() error {
	steamapps := filepath.Join(r.cfg.CS2Root, "steamapps")
	targets := []string{
		filepath.Join(steamapps, fmt.Sprintf("appmanifest_%d.acf", r.cfg.AppID)),
		filepath.Join(steamapps, "downloading"),
		filepath.Join(steamapps, "temp"),
	}
	for _, target := range targets {
		if err := os.RemoveAll(target); err != nil {
			return err
		}
		r.emit("info", "Deleted steamapps target before validate: %s", target)
	}
	return nil
}

func (r *Runtime) ensureMetamodPath() (map[string]any, error) {
	target := filepath.Join(r.cfg.CS2Root, "game", "csgo", "gameinfo.gi")
	content, err := os.ReadFile(target)
	if err != nil {
		return nil, err
	}
	updated, changed, err := insertMetamodSearchPath(string(content))
	if err != nil {
		return nil, err
	}
	if !changed {
		return map[string]any{"changed": false, "message": "Metamod path already exists"}, nil
	}
	if err := os.WriteFile(target, []byte(updated), 0o644); err != nil {
		return nil, err
	}
	return map[string]any{"changed": true, "message": "Metamod path inserted"}, nil
}

func insertMetamodSearchPath(content string) (string, bool, error) {
	newline := "\n"
	if strings.Contains(content, "\r\n") {
		newline = "\r\n"
	}
	normalized := strings.ReplaceAll(content, "\r\n", "\n")
	if regexp.MustCompile(`(?m)^[ \t]*Game[ \t]+csgo/addons/metamod(?:[ \t]*(?://.*)?)?$`).MatchString(normalized) {
		return content, false, nil
	}
	re := regexp.MustCompile(`(?m)^([ \t]*)Game([ \t]+)csgo(?:[ \t]*(?://.*)?)?$`)
	match := re.FindStringSubmatchIndex(normalized)
	if len(match) == 0 {
		return "", false, fmt.Errorf("Game csgo search path not found in gameinfo.gi")
	}
	parts := re.FindStringSubmatch(normalized[match[0]:match[1]])
	line := parts[1] + "Game" + parts[2] + "csgo/addons/metamod"
	updated := normalized[:match[0]] + line + "\n" + normalized[match[0]:]
	return strings.ReplaceAll(updated, "\n", newline), true, nil
}

func (r *Runtime) runProcess(ctx context.Context, timeout time.Duration, name string, args ...string) (string, error) {
	processCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(processCtx, name, args...)
	output, err := cmd.CombinedOutput()
	text := stripANSI(string(output))
	if processCtx.Err() == context.DeadlineExceeded {
		return text, fmt.Errorf("%s timed out after %s", name, timeout)
	}
	if err != nil {
		return text, fmt.Errorf("%s failed: %w: %s", name, err, text)
	}
	return text, nil
}

type processOutputEvent struct {
	level string
	text  string
	err   error
}

func (r *Runtime) runProcessWithLiveOutput(ctx context.Context, timeout time.Duration, name string, args ...string) (string, error) {
	return r.runProcessWithLiveOutputUntil(ctx, timeout, nil, name, args...)
}

func (r *Runtime) runProcessWithLiveOutputUntil(ctx context.Context, timeout time.Duration, stopCondition func(string, string) bool, name string, args ...string) (string, error) {
	processCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()

	cmd := exec.CommandContext(processCtx, name, args...)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return "", err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return "", err
	}
	if err := cmd.Start(); err != nil {
		return "", err
	}

	events := make(chan processOutputEvent, 100)
	var readers sync.WaitGroup
	readStream := func(level string, reader io.Reader) {
		defer readers.Done()
		scanner := bufio.NewScanner(reader)
		scanner.Split(scanLinesOrCarriageReturns)
		scanner.Buffer(make([]byte, 0, 64*1024), 1024*1024)
		for scanner.Scan() {
			events <- processOutputEvent{level: level, text: scanner.Text()}
		}
		if err := scanner.Err(); err != nil {
			events <- processOutputEvent{level: "error", err: err}
		}
	}

	readers.Add(2)
	go readStream("info", stdout)
	go readStream("error", stderr)

	waitCh := make(chan error, 1)
	go func() {
		readers.Wait()
		waitErr := cmd.Wait()
		waitCh <- waitErr
		close(waitCh)
		close(events)
	}()

	outputParts := []string{}
	var readErr error
	stoppedEarly := false
	for event := range events {
		if event.err != nil {
			if readErr == nil {
				readErr = event.err
			}
			continue
		}
		text := stripANSI(event.text)
		if text == "" {
			continue
		}
		outputParts = append(outputParts, text)
		if stopCondition != nil && stopCondition(event.level, text) {
			stoppedEarly = true
			if cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			continue
		}
		r.emit(event.level, "%s", text)
	}

	output := strings.TrimSpace(strings.Join(outputParts, "\n"))
	waitErr := <-waitCh
	if processCtx.Err() == context.DeadlineExceeded {
		return output, fmt.Errorf("%s timed out after %s", name, timeout)
	}
	if waitErr != nil && !stoppedEarly {
		return output, fmt.Errorf("%s failed: %w: %s", name, waitErr, output)
	}
	if readErr != nil {
		return output, fmt.Errorf("%s output read failed: %w", name, readErr)
	}
	return output, nil
}

func (r *Runtime) buildSteamcmdValidateStopCondition() func(string, string) bool {
	sawVerifyNearCompletion := false
	completionReported := false
	markComplete := func(message string) bool {
		if !completionReported {
			r.emit("info", "%s", message)
			completionReported = true
		}
		return true
	}
	return func(_ string, message string) bool {
		normalized := strings.TrimSpace(message)
		if normalized == "" {
			return false
		}
		if strings.Contains(strings.ToLower(normalized), "success!") && strings.Contains(strings.ToLower(normalized), "fully installed") {
			return markComplete("Detected steamcmd completion marker, stopping process tail")
		}
		matched := steamcmdValidateVerifyProgressRe.FindStringSubmatch(normalized)
		if len(matched) >= 2 {
			progress, err := strconv.ParseFloat(matched[1], 64)
			if err == nil {
				sawVerifyNearCompletion = progress >= 99
			}
			return false
		}
		if sawVerifyNearCompletion && steamcmdValidateUnknownStateRe.MatchString(normalized) {
			return markComplete("Detected steamcmd terminal unknown state after verify, treating validate as complete")
		}
		return false
	}
}

func scanLinesOrCarriageReturns(data []byte, atEOF bool) (advance int, token []byte, err error) {
	if atEOF && len(data) == 0 {
		return 0, nil, nil
	}
	if index := bytes.IndexAny(data, "\r\n"); index >= 0 {
		return index + 1, bytes.TrimRight(data[:index], "\r\n"), nil
	}
	if atEOF {
		return len(data), data, nil
	}
	return 0, nil, nil
}

var ansiEscape = regexp.MustCompile(`\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])`)
var steamcmdValidateVerifyProgressRe = regexp.MustCompile(`Update state \(0x5\) verifying install, progress: ([0-9]+(?:\.[0-9]+)?)`)
var steamcmdValidateUnknownStateRe = regexp.MustCompile(`Update state \(0x0\) unknown, progress: 0\.00 \(0 / 0\)`)

func stripANSI(input string) string {
	return strings.TrimSpace(ansiEscape.ReplaceAllString(input, ""))
}

func a2sErrorStatus(err error) string {
	if strings.Contains(strings.ToLower(err.Error()), "timeout") || strings.Contains(strings.ToLower(err.Error()), "deadline") {
		return "timeout"
	}
	return "error"
}

func (r *Runtime) profileStartKeys(profile config.MonitorProfile) []string {
	if len(profile.StartServerKeys) > 0 {
		return profile.StartServerKeys
	}
	keys := []string{}
	for _, server := range r.groups[profile.Key] {
		if server.StartAfterMonitor {
			keys = append(keys, server.Key)
		}
	}
	return keys
}

func (r *Runtime) defaultStartKeys(monitorServerKey string) []string {
	keys := []string{}
	monitor, ok := r.serversByKey[monitorServerKey]
	if ok && len(monitor.Groups) > 0 {
		for _, server := range r.groups[monitor.Groups[0]] {
			if server.StartAfterMonitor {
				keys = append(keys, server.Key)
			}
		}
	}
	if len(keys) > 0 {
		return keys
	}
	for _, server := range r.cfg.Servers {
		if server.StartAfterMonitor {
			keys = append(keys, server.Key)
		}
	}
	return keys
}

func keysFromServers(servers []config.Server) []string {
	keys := make([]string, 0, len(servers))
	for _, server := range servers {
		keys = append(keys, server.Key)
	}
	return keys
}

func appendUnique(values []string, value string) []string {
	if !slices.Contains(values, value) {
		return append(values, value)
	}
	return values
}

func tail(values []map[string]any, maxItems int) []map[string]any {
	if len(values) <= maxItems {
		return values
	}
	return values[len(values)-maxItems:]
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

func asInt(value any) int {
	switch typed := value.(type) {
	case int:
		return typed
	case int64:
		return int(typed)
	case float64:
		return int(typed)
	default:
		parsed, _ := strconv.Atoi(fmt.Sprint(typed))
		return parsed
	}
}

func nilIfEmpty(value string) any {
	if strings.TrimSpace(value) == "" {
		return nil
	}
	return value
}

func mapStringValue(values map[string]any, key string) string {
	value, ok := values[key]
	if !ok || value == nil {
		return ""
	}
	text := strings.TrimSpace(fmt.Sprint(value))
	if text == "<nil>" {
		return ""
	}
	return text
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
