package main

import (
	"context"
	"flag"
	"fmt"
	"log/slog"
	"os"
	"os/signal"
	"syscall"

	"github.com/kaishzz/kepagent/internal/agent"
	"github.com/kaishzz/kepagent/internal/api"
	"github.com/kaishzz/kepagent/internal/config"
	"github.com/kaishzz/kepagent/internal/dockerapi"
	"github.com/kaishzz/kepagent/internal/runtime"
	"github.com/kaishzz/kepagent/internal/version"
)

func main() {
	configPath := flag.String("config", "config.yaml", "Path to agent YAML config")
	showVersion := flag.Bool("version", false, "Show version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Printf("KepAgent %s\n", version.Version)
		return
	}

	logger := slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{Level: slog.LevelInfo}))
	slog.SetDefault(logger)

	cfg, err := config.Load(*configPath)
	if err != nil {
		logger.Error("failed to load config", "error", err)
		os.Exit(1)
	}

	apiClient := api.NewClient(cfg.APIBaseURL, cfg.APIKey, cfg.RequestTimeout())
	dockerClient := dockerapi.NewClient(cfg.DockerBaseURL, cfg.RequestTimeout())
	rt := runtime.New(cfg, dockerClient, logger)
	app := agent.New(cfg, apiClient, rt, logger)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := app.Run(ctx); err != nil {
		logger.Error("agent stopped", "error", err)
		os.Exit(1)
	}
}
