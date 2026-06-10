package config

import (
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	APIBaseURL                   string            `yaml:"api_base_url"`
	APIKey                       string            `yaml:"api_key"`
	PollIntervalSeconds          int               `yaml:"poll_interval_seconds"`
	HeartbeatIntervalSeconds     int               `yaml:"heartbeat_interval_seconds"`
	RequestTimeoutSeconds        int               `yaml:"request_timeout_seconds"`
	DockerBaseURL                string            `yaml:"docker_base_url"`
	GroupLabels                  map[string]string `yaml:"group_labels"`
	GroupOrder                   []string          `yaml:"group_order"`
	ServerQueryEnabled           bool              `yaml:"server_query_enabled"`
	ServerQueryHost              string            `yaml:"server_query_host"`
	ServerQueryTimeoutSeconds    int               `yaml:"server_query_timeout_seconds"`
	ServerQueryCacheTTLSeconds   int               `yaml:"server_query_cache_ttl_seconds"`
	RCONHost                     string            `yaml:"rcon_host"`
	RCONTimeoutSeconds           int               `yaml:"rcon_timeout_seconds"`
	SteamCMDPath                 string            `yaml:"steamcmd_sh"`
	CS2Root                      string            `yaml:"cs2_root"`
	AppID                        int               `yaml:"app_id"`
	MonitorServerKey             string            `yaml:"monitor_server_key"`
	MonitorPollIntervalSeconds   int               `yaml:"monitor_poll_interval_seconds"`
	MonitorStableSeconds         int               `yaml:"monitor_stable_seconds"`
	MonitorRecoverTimeoutSeconds int               `yaml:"monitor_recover_timeout_seconds"`
	MonitorRestartThreshold      int               `yaml:"monitor_restart_threshold"`
	MonitorProfiles              []MonitorProfile  `yaml:"monitor_profiles"`
	Servers                      []Server          `yaml:"servers"`
}

type MonitorProfile struct {
	Key              string   `yaml:"key"`
	MonitorServerKey string   `yaml:"monitor_server_key"`
	StartServerKeys  []string `yaml:"start_server_keys"`
}

type Server struct {
	Key               string            `yaml:"key"`
	CatalogServerID   string            `yaml:"catalog_server_id"`
	ContainerName     string            `yaml:"container_name"`
	Slot              int               `yaml:"slot"`
	Image             string            `yaml:"image"`
	Groups            []string          `yaml:"groups"`
	StartAfterMonitor bool              `yaml:"start_after_monitor"`
	Entrypoint        []string          `yaml:"entrypoint"`
	Command           []string          `yaml:"command"`
	Env               map[string]string `yaml:"env"`
	Ports             []PortBinding     `yaml:"ports"`
	Volumes           []VolumeBinding   `yaml:"volumes"`
	Labels            map[string]string `yaml:"labels"`
	WorkingDir        string            `yaml:"working_dir"`
	NetworkMode       string            `yaml:"network_mode"`
	StdinOpen         bool              `yaml:"stdin_open"`
	TTY               bool              `yaml:"tty"`
	RestartPolicy     string            `yaml:"restart_policy"`
}

func (s *Server) UnmarshalYAML(value *yaml.Node) error {
	type rawServer Server
	raw := rawServer{
		StartAfterMonitor: true,
		RestartPolicy:     "unless-stopped",
	}
	if err := value.Decode(&raw); err != nil {
		return err
	}
	*s = Server(raw)
	return nil
}

type PortBinding struct {
	HostPort      int    `yaml:"host_port"`
	ContainerPort int    `yaml:"container_port"`
	Protocol      string `yaml:"protocol"`
}

type VolumeBinding struct {
	HostPath      string `yaml:"host_path"`
	ContainerPath string `yaml:"container_path"`
	Mode          string `yaml:"mode"`
}

var envPattern = regexp.MustCompile(`\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}`)

func Load(path string) (*Config, error) {
	if err := loadDotenv(filepath.Join(filepath.Dir(path), ".env")); err != nil {
		return nil, err
	}
	if cwd, err := os.Getwd(); err == nil {
		_ = loadDotenv(filepath.Join(cwd, ".env"))
	}

	content, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	resolved, err := resolveEnv(string(content))
	if err != nil {
		return nil, err
	}

	cfg := defaultConfig()
	if err := yaml.Unmarshal([]byte(resolved), cfg); err != nil {
		return nil, err
	}
	cfg.normalize()
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	return cfg, nil
}

func defaultConfig() *Config {
	return &Config{
		PollIntervalSeconds:          1,
		HeartbeatIntervalSeconds:     1,
		RequestTimeoutSeconds:        15,
		GroupLabels:                  map[string]string{},
		GroupOrder:                   []string{},
		ServerQueryEnabled:           true,
		ServerQueryHost:              "127.0.0.1",
		ServerQueryTimeoutSeconds:    2,
		ServerQueryCacheTTLSeconds:   1,
		RCONHost:                     "127.0.0.1",
		RCONTimeoutSeconds:           5,
		SteamCMDPath:                 "/data/steamcmd/steamcmd.sh",
		CS2Root:                      "/data/cs2",
		AppID:                        730,
		MonitorPollIntervalSeconds:   5,
		MonitorStableSeconds:         120,
		MonitorRecoverTimeoutSeconds: 120,
		MonitorRestartThreshold:      2,
	}
}

func (c *Config) normalize() {
	c.APIBaseURL = strings.TrimRight(strings.TrimSpace(c.APIBaseURL), "/")
	c.APIKey = strings.TrimSpace(c.APIKey)
	c.DockerBaseURL = strings.TrimSpace(c.DockerBaseURL)
	if c.PollIntervalSeconds <= 0 {
		c.PollIntervalSeconds = 1
	}
	if c.HeartbeatIntervalSeconds <= 0 {
		c.HeartbeatIntervalSeconds = 1
	}
	if c.RequestTimeoutSeconds <= 0 {
		c.RequestTimeoutSeconds = 15
	}
	if c.ServerQueryHost == "" {
		c.ServerQueryHost = "127.0.0.1"
	}
	if c.ServerQueryTimeoutSeconds <= 0 {
		c.ServerQueryTimeoutSeconds = 2
	}
	if c.ServerQueryCacheTTLSeconds <= 0 {
		c.ServerQueryCacheTTLSeconds = 1
	}
	if c.RCONHost == "" {
		c.RCONHost = "127.0.0.1"
	}
	if c.RCONTimeoutSeconds <= 0 {
		c.RCONTimeoutSeconds = 5
	}
	if c.AppID <= 0 {
		c.AppID = 730
	}
	for i := range c.Servers {
		server := &c.Servers[i]
		if server.Groups == nil {
			server.Groups = []string{}
		}
		if server.Env == nil {
			server.Env = map[string]string{}
		}
		if server.Labels == nil {
			server.Labels = map[string]string{}
		}
		if server.RestartPolicy == "" {
			server.RestartPolicy = "unless-stopped"
		}
		for j := range server.Ports {
			if server.Ports[j].Protocol == "" {
				server.Ports[j].Protocol = "tcp"
			}
			server.Ports[j].Protocol = strings.ToLower(server.Ports[j].Protocol)
		}
		for j := range server.Volumes {
			if server.Volumes[j].Mode == "" {
				server.Volumes[j].Mode = "rw"
			}
		}
		if server.ContainerName == "" {
			mod := strings.TrimSpace(server.Labels["kepcs.mod"])
			if mod == "" && len(server.Groups) > 0 {
				mod = strings.TrimSpace(server.Groups[0])
			}
			if mod != "" && server.Slot > 0 {
				server.ContainerName = fmt.Sprintf("kepcs-%s-%d", mod, server.Slot)
			}
		}
	}
}

func (c *Config) validate() error {
	if c.APIBaseURL == "" {
		return fmt.Errorf("api_base_url is required")
	}
	if c.APIKey == "" {
		return fmt.Errorf("api_key is required")
	}
	seen := map[string]bool{}
	for _, server := range c.Servers {
		if strings.TrimSpace(server.Key) == "" {
			return fmt.Errorf("servers[].key is required")
		}
		if seen[server.Key] {
			return fmt.Errorf("duplicate server key %q", server.Key)
		}
		seen[server.Key] = true
		if server.ContainerName == "" {
			return fmt.Errorf("server %s needs container_name or slot plus kepcs.mod/groups", server.Key)
		}
		if server.Image == "" {
			return fmt.Errorf("server %s image is required", server.Key)
		}
	}
	return nil
}

func (c *Config) RequestTimeout() time.Duration {
	return time.Duration(c.RequestTimeoutSeconds) * time.Second
}

func (c *Config) PollInterval() time.Duration {
	return time.Duration(max(1, c.PollIntervalSeconds)) * time.Second
}

func (c *Config) HeartbeatInterval() time.Duration {
	return time.Duration(max(1, c.HeartbeatIntervalSeconds)) * time.Second
}

func (c *Config) ServerQueryTimeout() time.Duration {
	return time.Duration(max(1, c.ServerQueryTimeoutSeconds)) * time.Second
}

func (c *Config) RCONTimeout() time.Duration {
	return time.Duration(max(1, c.RCONTimeoutSeconds)) * time.Second
}

func resolveEnv(input string) (string, error) {
	var firstErr error
	output := envPattern.ReplaceAllStringFunc(input, func(raw string) string {
		if firstErr != nil {
			return raw
		}
		match := envPattern.FindStringSubmatch(raw)
		value, ok := os.LookupEnv(match[1])
		if ok {
			return value
		}
		if len(match) > 2 && match[2] != "" {
			return match[2]
		}
		firstErr = fmt.Errorf("missing environment variable %q", match[1])
		return raw
	})
	return output, firstErr
}

func loadDotenv(path string) error {
	content, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return nil
		}
		return err
	}
	for _, raw := range strings.Split(string(content), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		line = strings.TrimPrefix(line, "export ")
		key, value, ok := strings.Cut(line, "=")
		if !ok {
			continue
		}
		key = strings.TrimSpace(key)
		if key == "" {
			continue
		}
		value = strings.TrimSpace(value)
		if len(value) >= 2 && ((value[0] == '"' && value[len(value)-1] == '"') || (value[0] == '\'' && value[len(value)-1] == '\'')) {
			value = value[1 : len(value)-1]
		}
		if _, exists := os.LookupEnv(key); !exists {
			_ = os.Setenv(key, value)
		}
	}
	return nil
}

func (p *PortBinding) UnmarshalYAML(value *yaml.Node) error {
	type rawPort PortBinding
	var raw rawPort
	if err := value.Decode(&raw); err != nil {
		return err
	}
	*p = PortBinding(raw)
	return nil
}

func (c *Config) UnmarshalYAML(value *yaml.Node) error {
	type rawConfig Config
	var raw rawConfig = rawConfig(*defaultConfig())
	if err := decodeFlexible(value, &raw); err != nil {
		return err
	}
	*c = Config(raw)
	return nil
}

func decodeFlexible(node *yaml.Node, out any) error {
	content, err := yaml.Marshal(node)
	if err != nil {
		return err
	}
	content = []byte(normalizeQuotedNumbers(string(content)))
	return yaml.Unmarshal(content, out)
}

var quotedNumberLine = regexp.MustCompile(`(?m)^(\s*[A-Za-z0-9_]+:\s*)"(-?\d+)"\s*$`)

func normalizeQuotedNumbers(input string) string {
	return quotedNumberLine.ReplaceAllStringFunc(input, func(line string) string {
		match := quotedNumberLine.FindStringSubmatch(line)
		if len(match) != 3 {
			return line
		}
		if _, err := strconv.Atoi(match[2]); err != nil {
			return line
		}
		return match[1] + match[2]
	})
}
