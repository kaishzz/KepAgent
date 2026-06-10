package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadResolvesEnvAndDerivesContainerName(t *testing.T) {
	t.Setenv("KEPAGENT_API_BASE_URL", "https://example.test")
	t.Setenv("KEPAGENT_API_KEY", "secret")

	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	content := `
api_base_url: "${KEPAGENT_API_BASE_URL}"
api_key: "${KEPAGENT_API_KEY}"
servers:
  - key: "2102-1"
    slot: 1
    image: "steamrt3:latest"
    groups: ["2102"]
    ports:
      - host_port: 28010
        container_port: 28010
        protocol: "udp"
    labels:
      kepcs.mod: "2102"
`
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.APIBaseURL != "https://example.test" {
		t.Fatalf("unexpected api_base_url: %s", cfg.APIBaseURL)
	}
	if got := cfg.Servers[0].ContainerName; got != "kepcs-2102-1" {
		t.Fatalf("unexpected container name: %s", got)
	}
	if !cfg.Servers[0].StartAfterMonitor {
		t.Fatal("expected start_after_monitor to default to true")
	}
}
