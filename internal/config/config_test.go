package config

import (
	"os"
	"path/filepath"
	"strings"
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

func TestLoadExpandsDefaultsModesAndServers(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	content := `
api_base_url: "https://example.test"
api_key: "secret"
defaults:
  image: "steamrt3:latest"
  collection_id: "wrong-default"
  maxplayers: 10
  entrypoint: ["/entrypoint.sh"]
  stdin_open: true
  tty: true
  env:
    TZ: "Asia/Shanghai"
  volumes:
    - host_path: "/data/steamcmd"
      container_path: "/steamcmd"
      mode: "rw"
modes:
  "2102":
    collection_id: "3292908214"
    maxplayers: 64
    start_after_monitor: false
    env:
      MODE_ONLY: "1"
      TZ: "Mode/Timezone"
    volumes:
      - host_path: "/data/cs2"
        container_path: "/cs2"
        mode: "rw"
servers:
  - key: "2102-1"
    mode: "2102"
    slot: 1
    port: 28010
    start_after_monitor: true
    env:
      TZ: "Server/Timezone"
    volumes:
      - host_path: "/data/server-specific"
        container_path: "/server-specific"
        mode: "rw"
`
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	server := cfg.Servers[0]
	if server.Image != "steamrt3:latest" {
		t.Fatalf("unexpected image: %s", server.Image)
	}
	if server.ContainerName != "kepcs-2102-1" {
		t.Fatalf("unexpected container name: %s", server.ContainerName)
	}
	if len(server.Ports) != 2 || server.Ports[0].Protocol != "tcp" || server.Ports[1].Protocol != "udp" {
		t.Fatalf("unexpected ports: %#v", server.Ports)
	}
	if server.Labels["kepcs.mod"] != "2102" || server.Labels["kepcs.server_key"] != "2102-1" || server.Labels["kepcs.exec_cfg"] != "kepcs_2102_1.cfg" {
		t.Fatalf("unexpected labels: %#v", server.Labels)
	}
	if len(server.Volumes) != 3 {
		t.Fatalf("unexpected volumes: %#v", server.Volumes)
	}
	if server.Volumes[1].HostPath != "/data/cs2" || server.Volumes[2].HostPath != "/data/server-specific" {
		t.Fatalf("unexpected volume precedence: %#v", server.Volumes)
	}
	if server.Env["TZ"] != "Server/Timezone" || server.Env["MODE_ONLY"] != "1" {
		t.Fatalf("unexpected env precedence: %#v", server.Env)
	}
	if !server.StartAfterMonitor {
		t.Fatal("server start_after_monitor should override mode default")
	}
	if len(server.Command) != 3 || !strings.Contains(server.Command[2], "-port 28010") || !strings.Contains(server.Command[2], "+exec kepcs_2102_1.cfg") {
		t.Fatalf("unexpected command: %#v", server.Command)
	}
	if !strings.Contains(server.Command[2], "+host_workshop_collection 3292908214") || !strings.Contains(server.Command[2], "-maxplayers 64") {
		t.Fatalf("mode values did not override defaults: %s", server.Command[2])
	}
}

func TestServerOverridesModeAndDefaults(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	content := `
api_base_url: "https://example.test"
api_key: "secret"
defaults:
  image: "steamrt3:latest"
  stdin_open: true
  tty: true
  env:
    SHARED: "default"
    DEFAULT_ONLY: "1"
  labels:
    source: "default"
  ports:
    - host_port: 1000
      container_port: 1000
      protocol: "tcp"
  volumes:
    - host_path: "/default"
      container_path: "/data/default"
modes:
  "2102":
    stdin_open: false
    tty: false
    env:
      SHARED: "mode"
      MODE_ONLY: "1"
    labels:
      source: "mode"
      mode_only: "1"
    ports:
      - host_port: 2000
        container_port: 2000
        protocol: "udp"
    volumes:
      - host_path: "/mode"
        container_path: "/data/mode"
servers:
  - key: "2102-1"
    mode: "2102"
    container_name: "kepcs-2102-1"
    stdin_open: true
    tty: true
    env:
      SHARED: "server"
      SERVER_ONLY: "1"
    labels:
      source: "server"
      server_only: "1"
    ports:
      - host_port: 3000
        container_port: 3000
        protocol: "tcp"
    volumes:
      - host_path: "/server"
        container_path: "/data/server"
`
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	server := cfg.Servers[0]
	if server.Env["DEFAULT_ONLY"] != "1" || server.Env["MODE_ONLY"] != "1" || server.Env["SERVER_ONLY"] != "1" || server.Env["SHARED"] != "server" {
		t.Fatalf("unexpected env merge: %#v", server.Env)
	}
	if server.Labels["mode_only"] != "1" || server.Labels["server_only"] != "1" || server.Labels["source"] != "server" {
		t.Fatalf("unexpected label merge: %#v", server.Labels)
	}
	if len(server.Ports) != 3 || server.Ports[0].HostPort != 1000 || server.Ports[1].HostPort != 2000 || server.Ports[2].HostPort != 3000 {
		t.Fatalf("unexpected port merge: %#v", server.Ports)
	}
	if len(server.Volumes) != 3 || server.Volumes[0].HostPath != "/default" || server.Volumes[1].HostPath != "/mode" || server.Volumes[2].HostPath != "/server" {
		t.Fatalf("unexpected volume merge: %#v", server.Volumes)
	}
	if !server.StdinOpen || !server.TTY {
		t.Fatalf("server bool overrides should win: stdin_open=%v tty=%v", server.StdinOpen, server.TTY)
	}
}

func TestPrivateConfigsLoadIfPresent(t *testing.T) {
	t.Setenv("KEPAGENT_API_BASE_URL", "https://example.test")
	t.Setenv("KEPAGENT_API_KEY", "secret")

	for _, path := range []string{
		filepath.Join("..", "..", "config1.yaml"),
		filepath.Join("..", "..", "config2.yaml"),
	} {
		if _, err := os.Stat(path); os.IsNotExist(err) {
			t.Skipf("%s is not present", path)
		}
		cfg, err := Load(path)
		if err != nil {
			t.Fatalf("load %s: %v", path, err)
		}
		if len(cfg.Servers) == 0 {
			t.Fatalf("%s has no servers", path)
		}
		for _, server := range cfg.Servers {
			if server.Image == "" || len(server.Command) == 0 || len(server.Ports) == 0 {
				t.Fatalf("%s did not expand server %s: %#v", path, server.Key, server)
			}
		}
	}
}

func TestLoadReplayTargets(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	content := `
api_base_url: "https://example.test"
api_key: "secret"
replay_targets:
  - key: "surf-main"
    mode_key: "surf"
    label: "Surf 主服 Replay"
    path: "/data/replays/surf"
    enabled: true
    allow_upload: true
    allow_download: true
    max_upload_size_mb: 128
    transfer_limit_mbps: 5
    concurrency_limit: 1
servers:
  - key: "2102-1"
    mode: "2102"
    container_name: "kepcs-2102-1"
    image: "steamrt3:latest"
`
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}

	cfg, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(cfg.ReplayTargets) != 1 {
		t.Fatalf("unexpected replay target count: %d", len(cfg.ReplayTargets))
	}
	target := cfg.ReplayTargets[0]
	if target.Key != "surf-main" || target.Path != filepath.Clean("/data/replays/surf") || target.MaxUploadSizeMB != 128 {
		t.Fatalf("unexpected replay target: %#v", target)
	}
}
