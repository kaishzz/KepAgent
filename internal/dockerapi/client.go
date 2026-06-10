package dockerapi

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

type Client struct {
	baseURL string
	http    *http.Client
}

func NewClient(baseURL string, timeout time.Duration) *Client {
	baseURL = strings.TrimSpace(baseURL)
	if baseURL == "" {
		baseURL = "unix:///var/run/docker.sock"
	}

	client := &http.Client{Timeout: timeout}
	if strings.HasPrefix(baseURL, "unix://") {
		socketPath := strings.TrimPrefix(baseURL, "unix://")
		client.Transport = &http.Transport{
			DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
				return (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
			},
		}
		baseURL = "http://docker"
	}

	return &Client{baseURL: strings.TrimRight(baseURL, "/"), http: client}
}

func (c *Client) InspectContainer(ctx context.Context, name string) (*ContainerInspect, error) {
	var out ContainerInspect
	err := c.request(ctx, http.MethodGet, "/containers/"+url.PathEscape(name)+"/json", nil, &out)
	if isNotFound(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) CreateContainer(ctx context.Context, name string, req CreateContainerRequest) (string, error) {
	var out struct {
		ID       string   `json:"Id"`
		Warnings []string `json:"Warnings"`
	}
	path := "/containers/create?name=" + url.QueryEscape(name)
	err := c.request(ctx, http.MethodPost, path, req, &out)
	if err != nil && strings.Contains(strings.ToLower(err.Error()), "no such image") {
		if pullErr := c.PullImage(ctx, req.Image); pullErr != nil {
			return "", pullErr
		}
		err = c.request(ctx, http.MethodPost, path, req, &out)
	}
	return out.ID, err
}

func (c *Client) StartContainer(ctx context.Context, idOrName string) error {
	return c.request(ctx, http.MethodPost, "/containers/"+url.PathEscape(idOrName)+"/start", nil, nil)
}

func (c *Client) StopContainer(ctx context.Context, idOrName string, timeoutSeconds int) error {
	path := fmt.Sprintf("/containers/%s/stop?t=%d", url.PathEscape(idOrName), timeoutSeconds)
	err := c.request(ctx, http.MethodPost, path, nil, nil)
	if isNotModified(err) || isNotFound(err) {
		return nil
	}
	return err
}

func (c *Client) RemoveContainer(ctx context.Context, idOrName string, force bool) error {
	path := "/containers/" + url.PathEscape(idOrName)
	if force {
		path += "?force=true"
	}
	err := c.request(ctx, http.MethodDelete, path, nil, nil)
	if isNotFound(err) {
		return nil
	}
	return err
}

func (c *Client) PullImage(ctx context.Context, image string) error {
	path := "/images/create?fromImage=" + url.QueryEscape(image)
	return c.request(ctx, http.MethodPost, path, nil, nil)
}

func (c *Client) request(ctx context.Context, method, path string, payload any, out any) error {
	var body io.Reader
	if payload != nil {
		content, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(content)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return err
	}
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	content, err := io.ReadAll(io.LimitReader(resp.Body, 16*1024*1024))
	if err != nil {
		return err
	}
	if resp.StatusCode >= 400 {
		return dockerError{statusCode: resp.StatusCode, message: strings.TrimSpace(string(content))}
	}
	if out != nil && len(bytes.TrimSpace(content)) > 0 {
		if err := json.Unmarshal(content, out); err != nil {
			return err
		}
	}
	return nil
}

type dockerError struct {
	statusCode int
	message    string
}

func (e dockerError) Error() string {
	if e.message != "" {
		return fmt.Sprintf("docker api failed: %d %s", e.statusCode, e.message)
	}
	return fmt.Sprintf("docker api failed: %d", e.statusCode)
}

func isNotFound(err error) bool {
	if value, ok := err.(dockerError); ok {
		return value.statusCode == http.StatusNotFound
	}
	return false
}

func isNotModified(err error) bool {
	if value, ok := err.(dockerError); ok {
		return value.statusCode == http.StatusNotModified
	}
	return false
}
