package dockerapi

type ContainerInspect struct {
	ID              string          `json:"Id"`
	Image           string          `json:"Image"`
	Name            string          `json:"Name"`
	RestartCount    int             `json:"RestartCount"`
	Config          ContainerConfig `json:"Config"`
	State           ContainerState  `json:"State"`
	NetworkSettings NetworkSettings `json:"NetworkSettings"`
}

type ContainerConfig struct {
	Image  string            `json:"Image"`
	Labels map[string]string `json:"Labels"`
}

type ContainerState struct {
	Status       string `json:"Status"`
	Running      bool   `json:"Running"`
	RestartCount int    `json:"RestartCount"`
}

type NetworkSettings struct {
	Ports map[string][]PortBinding `json:"Ports"`
}

type PortBinding struct {
	HostIP   string `json:"HostIp,omitempty"`
	HostPort string `json:"HostPort,omitempty"`
}

type CreateContainerRequest struct {
	Image        string              `json:"Image"`
	Cmd          []string            `json:"Cmd,omitempty"`
	Entrypoint   []string            `json:"Entrypoint,omitempty"`
	Env          []string            `json:"Env,omitempty"`
	Labels       map[string]string   `json:"Labels,omitempty"`
	WorkingDir   string              `json:"WorkingDir,omitempty"`
	OpenStdin    bool                `json:"OpenStdin,omitempty"`
	Tty          bool                `json:"Tty,omitempty"`
	ExposedPorts map[string]struct{} `json:"ExposedPorts,omitempty"`
	HostConfig   HostConfig          `json:"HostConfig"`
}

type HostConfig struct {
	Binds         []string                 `json:"Binds,omitempty"`
	PortBindings  map[string][]PortBinding `json:"PortBindings,omitempty"`
	NetworkMode   string                   `json:"NetworkMode,omitempty"`
	RestartPolicy RestartPolicy            `json:"RestartPolicy,omitempty"`
}

type RestartPolicy struct {
	Name string `json:"Name,omitempty"`
}
