package config

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"text/template"
	"time"

	"gopkg.in/yaml.v3"
)

type Config struct {
	APIBaseURL                   string            `yaml:"api_base_url"`
	APIKey                       string            `yaml:"api_key"`
	KomariInstanceID             string            `yaml:"komari_instance_id"`
	PollIntervalSeconds          int               `yaml:"poll_interval_seconds"`
	HeartbeatIntervalSeconds     int               `yaml:"heartbeat_interval_seconds"`
	RequestTimeoutSeconds        int               `yaml:"request_timeout_seconds"`
	DockerBaseURL                string            `yaml:"docker_base_url"`
	DockerProxyURL               string            `yaml:"docker_proxy_url"`
	GroupLabels                  map[string]string `yaml:"group_labels"`
	GroupOrder                   []string          `yaml:"group_order"`
	ServerQueryEnabled           bool              `yaml:"server_query_enabled"`
	ServerQueryHost              string            `yaml:"server_query_host"`
	ServerQueryTimeoutSeconds    int               `yaml:"server_query_timeout_seconds"`
	ServerQueryCacheTTLSeconds   int               `yaml:"server_query_cache_ttl_seconds"`
	RCONTimeoutSeconds           int               `yaml:"rcon_timeout_seconds"`
	SteamCMDPath                 string            `yaml:"steamcmd_sh"`
	CS2Root                      string            `yaml:"cs2_root"`
	AppID                        int               `yaml:"app_id"`
	MonitorServerKey             string            `yaml:"monitor_server_key"`
	MonitorPollIntervalSeconds   int               `yaml:"monitor_poll_interval_seconds"`
	MonitorStableSeconds         int               `yaml:"monitor_stable_seconds"`
	MonitorRecoverTimeoutSeconds int               `yaml:"monitor_recover_timeout_seconds"`
	MonitorRestartThreshold      int               `yaml:"monitor_restart_threshold"`
	ReplayTempDir                string            `yaml:"replay_temp_dir"`
	ReplayTargets                []ReplayTarget    `yaml:"replay_targets"`
	MonitorProfiles              []MonitorProfile  `yaml:"monitor_profiles"`
	Defaults                     ServerDefaults    `yaml:"defaults"`
	Modes                        map[string]Mode   `yaml:"modes"`
	Servers                      []Server          `yaml:"servers"`
}

type ReplayTarget struct {
	Key                 string `yaml:"key"`
	ModeKey             string `yaml:"mode_key"`
	Label               string `yaml:"label"`
	Path                string `yaml:"path"`
	Enabled             bool   `yaml:"enabled"`
	AllowUpload         bool   `yaml:"allow_upload"`
	AllowDownload       bool   `yaml:"allow_download"`
	MaxUploadSizeMB     int    `yaml:"max_upload_size_mb"`
	TransferLimitMbps   int    `yaml:"transfer_limit_mbps"`
	ConcurrencyLimit    int    `yaml:"concurrency_limit"`
}

type MonitorProfile struct {
	Key              string   `yaml:"key"`
	MonitorServerKey string   `yaml:"monitor_server_key"`
	StartServerKeys  []string `yaml:"start_server_keys"`
}

type Server struct {
	Key               string            `yaml:"key"`
	Mode              string            `yaml:"mode"`
	CatalogServerID   string            `yaml:"catalog_server_id"`
	ContainerName     string            `yaml:"container_name"`
	Slot              int               `yaml:"slot"`
	Port              int               `yaml:"port"`
	ExecConfig        string            `yaml:"exec_cfg"`
	WorkshopMapID     string            `yaml:"workshop_map_id"`
	MaxPlayers        int               `yaml:"maxplayers"`
	Image             string            `yaml:"image"`
	Groups            []string          `yaml:"groups"`
	StartAfterMonitor bool              `yaml:"start_after_monitor"`
	Entrypoint        []string          `yaml:"entrypoint"`
	Command           []string          `yaml:"command"`
	CommandTemplate   []string          `yaml:"command_template"`
	Env               map[string]string `yaml:"env"`
	Ports             []PortBinding     `yaml:"ports"`
	Volumes           []VolumeBinding   `yaml:"volumes"`
	Labels            map[string]string `yaml:"labels"`
	WorkingDir        string            `yaml:"working_dir"`
	NetworkMode       string            `yaml:"network_mode"`
	StdinOpen         bool              `yaml:"stdin_open"`
	TTY               bool              `yaml:"tty"`
	RestartPolicy     string            `yaml:"restart_policy"`

	startAfterMonitorSet bool
	stdinOpenSet         bool
	ttySet               bool
}

type ServerDefaults struct {
	Image             string            `yaml:"image"`
	Entrypoint        []string          `yaml:"entrypoint"`
	Command           []string          `yaml:"command"`
	CommandTemplate   []string          `yaml:"command_template"`
	Env               map[string]string `yaml:"env"`
	Ports             []PortBinding     `yaml:"ports"`
	Volumes           []VolumeBinding   `yaml:"volumes"`
	Labels            map[string]string `yaml:"labels"`
	WorkingDir        string            `yaml:"working_dir"`
	NetworkMode       string            `yaml:"network_mode"`
	StdinOpen         bool              `yaml:"stdin_open"`
	TTY               bool              `yaml:"tty"`
	RestartPolicy     string            `yaml:"restart_policy"`
	StartAfterMonitor *bool             `yaml:"start_after_monitor"`
	MaxPlayers        int               `yaml:"maxplayers"`
	WorkshopMapID     string            `yaml:"workshop_map_id"`
}

func (d *ServerDefaults) UnmarshalYAML(value *yaml.Node) error {
	type rawDefaults ServerDefaults
	raw := rawDefaults{
		RestartPolicy: "unless-stopped",
	}
	if err := value.Decode(&raw); err != nil {
		return err
	}
	*d = ServerDefaults(raw)
	return nil
}

type Mode struct {
	Label             string            `yaml:"label"`
	Groups            []string          `yaml:"groups"`
	WorkshopMapID     string            `yaml:"workshop_map_id"`
	MaxPlayers        int               `yaml:"maxplayers"`
	CommandTemplate   []string          `yaml:"command_template"`
	Command           []string          `yaml:"command"`
	Env               map[string]string `yaml:"env"`
	Ports             []PortBinding     `yaml:"ports"`
	Volumes           []VolumeBinding   `yaml:"volumes"`
	Labels            map[string]string `yaml:"labels"`
	WorkingDir        string            `yaml:"working_dir"`
	NetworkMode       string            `yaml:"network_mode"`
	StdinOpen         *bool             `yaml:"stdin_open"`
	TTY               *bool             `yaml:"tty"`
	StartAfterMonitor *bool             `yaml:"start_after_monitor"`
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
	s.startAfterMonitorSet = mappingHasKey(value, "start_after_monitor")
	s.stdinOpenSet = mappingHasKey(value, "stdin_open")
	s.ttySet = mappingHasKey(value, "tty")
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
		RCONTimeoutSeconds:           5,
		SteamCMDPath:                 "/data/steamcmd/steamcmd.sh",
		CS2Root:                      "/data/cs2",
		AppID:                        730,
		MonitorPollIntervalSeconds:   5,
		MonitorStableSeconds:         120,
		MonitorRecoverTimeoutSeconds: 120,
		MonitorRestartThreshold:      2,
		ReplayTempDir:                filepath.Join(os.TempDir(), "kepagent-replay"),
		Modes:                        map[string]Mode{},
	}
}

func (c *Config) normalize() {
	c.APIBaseURL = strings.TrimRight(strings.TrimSpace(c.APIBaseURL), "/")
	c.APIKey = strings.TrimSpace(c.APIKey)
	c.KomariInstanceID = strings.TrimSpace(c.KomariInstanceID)
	c.DockerBaseURL = strings.TrimSpace(c.DockerBaseURL)
	c.DockerProxyURL = strings.TrimSpace(c.DockerProxyURL)
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
	if c.RCONTimeoutSeconds <= 0 {
		c.RCONTimeoutSeconds = 5
	}
	if c.AppID <= 0 {
		c.AppID = 730
	}
	if strings.TrimSpace(c.ReplayTempDir) == "" {
		c.ReplayTempDir = filepath.Join(os.TempDir(), "kepagent-replay")
	}
	for i := range c.ReplayTargets {
		target := &c.ReplayTargets[i]
		target.Key = strings.TrimSpace(target.Key)
		target.ModeKey = strings.TrimSpace(target.ModeKey)
		target.Label = strings.TrimSpace(target.Label)
		target.Path = filepath.Clean(strings.TrimSpace(target.Path))
		if !target.Enabled {
			target.Enabled = target.Path != "."
		}
		if target.MaxUploadSizeMB <= 0 {
			target.MaxUploadSizeMB = 256
		}
		if target.ConcurrencyLimit <= 0 {
			target.ConcurrencyLimit = 1
		}
	}
	c.applyServerDefaults()
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
		applyDockerProxyEnv(server, c.DockerProxyURL)
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

func (c *Config) applyServerDefaults() {
	for i := range c.Servers {
		server := &c.Servers[i]
		original := *server
		if strings.TrimSpace(server.Mode) == "" && len(server.Groups) > 0 {
			server.Mode = strings.TrimSpace(server.Groups[0])
			original.Mode = server.Mode
		}
		mode := c.Modes[strings.TrimSpace(server.Mode)]
		applyDefaults(server, c.Defaults)
		applyMode(server, mode, original)
		finalizeServer(server)
	}
}

func applyDefaults(server *Server, defaults ServerDefaults) {
	if server.Image == "" {
		server.Image = defaults.Image
	}
	if len(server.Entrypoint) == 0 {
		server.Entrypoint = cloneStrings(defaults.Entrypoint)
	}
	if len(server.Command) == 0 && len(defaults.Command) > 0 {
		server.Command = cloneStrings(defaults.Command)
	}
	if len(server.CommandTemplate) == 0 {
		server.CommandTemplate = cloneStrings(defaults.CommandTemplate)
	}
	if len(server.Env) == 0 {
		server.Env = cloneMap(defaults.Env)
	} else {
		server.Env = mergeMap(defaults.Env, server.Env)
	}
	if len(server.Ports) == 0 {
		server.Ports = clonePorts(defaults.Ports)
	} else {
		server.Ports = append(clonePorts(defaults.Ports), server.Ports...)
	}
	if len(server.Volumes) == 0 {
		server.Volumes = cloneVolumes(defaults.Volumes)
	} else {
		server.Volumes = append(cloneVolumes(defaults.Volumes), server.Volumes...)
	}
	if len(server.Labels) == 0 {
		server.Labels = cloneMap(defaults.Labels)
	} else {
		server.Labels = mergeMap(defaults.Labels, server.Labels)
	}
	if server.WorkingDir == "" {
		server.WorkingDir = defaults.WorkingDir
	}
	if server.NetworkMode == "" {
		server.NetworkMode = defaults.NetworkMode
	}
	if defaults.StdinOpen && !server.stdinOpenSet {
		server.StdinOpen = defaults.StdinOpen
	}
	if defaults.TTY && !server.ttySet {
		server.TTY = defaults.TTY
	}
	if server.RestartPolicy == "" {
		server.RestartPolicy = defaults.RestartPolicy
	}
	if defaults.StartAfterMonitor != nil && !server.startAfterMonitorSet {
		server.StartAfterMonitor = *defaults.StartAfterMonitor
	}
	if server.MaxPlayers <= 0 {
		server.MaxPlayers = defaults.MaxPlayers
	}
	if server.WorkshopMapID == "" {
		server.WorkshopMapID = defaults.WorkshopMapID
	}
}

func applyMode(server *Server, mode Mode, original Server) {
	if len(mode.Groups) > 0 && len(server.Groups) == 0 {
		server.Groups = cloneStrings(mode.Groups)
	}
	if original.WorkshopMapID == "" && mode.WorkshopMapID != "" {
		server.WorkshopMapID = mode.WorkshopMapID
	}
	if original.MaxPlayers <= 0 && mode.MaxPlayers > 0 {
		server.MaxPlayers = mode.MaxPlayers
	}
	if len(mode.Env) > 0 {
		server.Env = mergeMap(mergeMap(server.Env, mode.Env), original.Env)
	}
	if len(mode.Ports) > 0 {
		server.Ports = append(withoutTrailingPorts(server.Ports, original.Ports), clonePorts(mode.Ports)...)
		server.Ports = append(server.Ports, clonePorts(original.Ports)...)
	}
	if len(mode.Volumes) > 0 {
		server.Volumes = append(withoutTrailingVolumes(server.Volumes, original.Volumes), cloneVolumes(mode.Volumes)...)
		server.Volumes = append(server.Volumes, cloneVolumes(original.Volumes)...)
	}
	if len(mode.Labels) > 0 {
		server.Labels = mergeMap(mergeMap(server.Labels, mode.Labels), original.Labels)
	}
	if original.WorkingDir == "" && mode.WorkingDir != "" {
		server.WorkingDir = mode.WorkingDir
	}
	if original.NetworkMode == "" && mode.NetworkMode != "" {
		server.NetworkMode = mode.NetworkMode
	}
	if mode.StdinOpen != nil && !original.stdinOpenSet {
		server.StdinOpen = *mode.StdinOpen
	}
	if mode.TTY != nil && !original.ttySet {
		server.TTY = *mode.TTY
	}
	if len(original.Command) == 0 && len(mode.Command) > 0 {
		server.Command = cloneStrings(mode.Command)
	}
	if len(original.CommandTemplate) == 0 && len(mode.CommandTemplate) > 0 {
		server.CommandTemplate = cloneStrings(mode.CommandTemplate)
	}
	if mode.StartAfterMonitor != nil && !original.startAfterMonitorSet {
		server.StartAfterMonitor = *mode.StartAfterMonitor
	}
}

func finalizeServer(server *Server) {
	if len(server.Groups) == 0 && strings.TrimSpace(server.Mode) != "" {
		server.Groups = []string{strings.TrimSpace(server.Mode)}
	}
	if server.Labels == nil {
		server.Labels = map[string]string{}
	}
	if server.Mode != "" && server.Labels["kepcs.mod"] == "" {
		server.Labels["kepcs.mod"] = server.Mode
	}
	if server.Key != "" && server.Labels["kepcs.server_key"] == "" {
		server.Labels["kepcs.server_key"] = server.Key
	}
	if server.ExecConfig == "" && server.Slot > 0 {
		mode := firstNonEmpty(server.Mode, firstString(server.Groups))
		if mode != "" {
			server.ExecConfig = fmt.Sprintf("kepcs_%s_%d.cfg", mode, server.Slot)
		}
	}
	if server.ExecConfig != "" && server.Labels["kepcs.exec_cfg"] == "" {
		server.Labels["kepcs.exec_cfg"] = server.ExecConfig
	}
	if server.Port > 0 && len(server.Ports) == 0 {
		server.Ports = []PortBinding{
			{HostPort: server.Port, ContainerPort: server.Port, Protocol: "tcp"},
			{HostPort: server.Port, ContainerPort: server.Port, Protocol: "udp"},
		}
	}
	if len(server.Command) == 0 {
		template := server.CommandTemplate
		if len(template) == 0 {
			template = defaultCommandTemplate()
		}
		server.Command = renderCommand(template, *server)
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
	replaySeen := map[string]bool{}
	for _, target := range c.ReplayTargets {
		if target.Key == "" {
			return fmt.Errorf("replay_targets[].key is required")
		}
		if replaySeen[target.Key] {
			return fmt.Errorf("duplicate replay target key %q", target.Key)
		}
		replaySeen[target.Key] = true
		if target.Path == "" || target.Path == "." {
			return fmt.Errorf("replay target %s path is required", target.Key)
		}
	}
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

func (c *Config) UnmarshalYAML(value *yaml.Node) error {
	type rawConfig Config
	var raw rawConfig = rawConfig(*defaultConfig())
	if err := value.Decode(&raw); err != nil {
		return err
	}
	*c = Config(raw)
	return nil
}

func mappingHasKey(value *yaml.Node, key string) bool {
	if value == nil || value.Kind != yaml.MappingNode {
		return false
	}
	for index := 0; index+1 < len(value.Content); index += 2 {
		if value.Content[index].Value == key {
			return true
		}
	}
	return false
}

func renderCommand(command []string, server Server) []string {
	data := map[string]any{
		"key":           server.Key,
		"mode":          firstNonEmpty(server.Mode, firstString(server.Groups)),
		"group":         firstNonEmpty(server.Mode, firstString(server.Groups)),
		"slot":          server.Slot,
		"port":          server.Port,
		"exec_cfg":      server.ExecConfig,
		"workshop_map_id": server.WorkshopMapID,
		"maxplayers":    server.MaxPlayers,
	}
	rendered := make([]string, 0, len(command))
	for _, item := range command {
		tpl, err := template.New("command").Option("missingkey=zero").Parse(item)
		if err != nil {
			rendered = append(rendered, item)
			continue
		}
		var buffer bytes.Buffer
		if err := tpl.Execute(&buffer, data); err != nil {
			rendered = append(rendered, item)
			continue
		}
		rendered = append(rendered, buffer.String())
	}
	return rendered
}

func defaultCommandTemplate() []string {
	return []string{
		"bash",
		"-lc",
		"cd /cs2/game/bin/linuxsteamrt64 && exec ./cs2 -dedicated -console -high -maxplayers {{.maxplayers}} +game_type 0 +game_mode 0 +map de_dust2 -port {{.port}} -ip 0.0.0.0 -disable_workshop_command_filtering +host_workshop_map {{.workshop_map_id}} +exec {{.exec_cfg}}",
	}
}

func applyDockerProxyEnv(server *Server, proxyURL string) {
	proxyURL = strings.TrimSpace(proxyURL)
	if proxyURL == "" {
		return
	}
	if server.Env == nil {
		server.Env = map[string]string{}
	}
	for _, key := range []string{"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"} {
		if strings.TrimSpace(server.Env[key]) == "" {
			server.Env[key] = proxyURL
		}
	}
}

func mergeMap(base map[string]string, override map[string]string) map[string]string {
	result := cloneMap(base)
	if result == nil {
		result = map[string]string{}
	}
	for key, value := range override {
		result[key] = value
	}
	return result
}

func cloneMap(input map[string]string) map[string]string {
	if input == nil {
		return nil
	}
	output := make(map[string]string, len(input))
	for key, value := range input {
		output[key] = value
	}
	return output
}

func cloneStrings(input []string) []string {
	if input == nil {
		return nil
	}
	return append([]string(nil), input...)
}

func clonePorts(input []PortBinding) []PortBinding {
	if input == nil {
		return nil
	}
	return append([]PortBinding(nil), input...)
}

func withoutTrailingPorts(values []PortBinding, trailing []PortBinding) []PortBinding {
	if len(trailing) == 0 || len(values) < len(trailing) {
		return clonePorts(values)
	}
	return clonePorts(values[:len(values)-len(trailing)])
}

func cloneVolumes(input []VolumeBinding) []VolumeBinding {
	if input == nil {
		return nil
	}
	return append([]VolumeBinding(nil), input...)
}

func withoutTrailingVolumes(values []VolumeBinding, trailing []VolumeBinding) []VolumeBinding {
	if len(trailing) == 0 || len(values) < len(trailing) {
		return cloneVolumes(values)
	}
	return cloneVolumes(values[:len(values)-len(trailing)])
}

func firstString(values []string) string {
	if len(values) == 0 {
		return ""
	}
	return strings.TrimSpace(values[0])
}

func firstNonEmpty(values ...string) string {
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value != "" {
			return value
		}
	}
	return ""
}
