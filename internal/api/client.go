package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

type Client struct {
	baseURL string
	apiKey  string
	http    *http.Client
}

type Response map[string]any

type Command struct {
	ID            string         `json:"id"`
	CommandType   string         `json:"commandType"`
	Payload       map[string]any `json:"payload"`
	Status        string         `json:"status"`
	CancelRequest map[string]any `json:"cancelRequest"`
}

type LogEntry struct {
	Level   string `json:"level,omitempty"`
	Message string `json:"message"`
}

func NewClient(baseURL, apiKey string, timeout time.Duration) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		apiKey:  apiKey,
		http:    &http.Client{Timeout: timeout},
	}
}

func (c *Client) FetchMe(ctx context.Context) (Response, error) {
	return c.request(ctx, http.MethodGet, "/agent/api/me", nil)
}

func (c *Client) SendHeartbeat(ctx context.Context, payload map[string]any) (Response, error) {
	return c.request(ctx, http.MethodPost, "/agent/api/heartbeat", payload)
}

func (c *Client) ClaimCommand(ctx context.Context) (*Command, error) {
	data, err := c.request(ctx, http.MethodPost, "/agent/api/commands/claim", nil)
	if err != nil {
		return nil, err
	}
	raw, ok := data["command"]
	if !ok || raw == nil {
		return nil, nil
	}
	var command Command
	if err := decodeJSONValue(raw, &command); err != nil {
		return nil, err
	}
	if command.Payload == nil {
		command.Payload = map[string]any{}
	}
	return &command, nil
}

func (c *Client) FetchCommand(ctx context.Context, id string) (*Command, error) {
	data, err := c.request(ctx, http.MethodGet, "/agent/api/commands/"+id, nil)
	if err != nil {
		return nil, err
	}
	raw, ok := data["command"]
	if !ok || raw == nil {
		return nil, nil
	}
	var command Command
	if err := decodeJSONValue(raw, &command); err != nil {
		return nil, err
	}
	return &command, nil
}

func (c *Client) MarkCommandStarted(ctx context.Context, id string) (Response, error) {
	return c.request(ctx, http.MethodPost, "/agent/api/commands/"+id+"/start", nil)
}

func (c *Client) AppendCommandLogs(ctx context.Context, id string, logs []LogEntry) (Response, error) {
	return c.request(ctx, http.MethodPost, "/agent/api/commands/"+id+"/logs", map[string]any{"logs": logs})
}

func (c *Client) FinishCommand(ctx context.Context, id string, success bool, result any, errorMessage string, cancelled bool) (Response, error) {
	payload := map[string]any{
		"success":   success,
		"cancelled": cancelled,
	}
	if result != nil {
		payload["result"] = result
	}
	if strings.TrimSpace(errorMessage) != "" {
		payload["errorMessage"] = errorMessage
	}
	return c.request(ctx, http.MethodPost, "/agent/api/commands/"+id+"/finish", payload)
}

func (c *Client) request(ctx context.Context, method, path string, payload any) (Response, error) {
	var body io.Reader
	if payload != nil {
		content, err := json.Marshal(payload)
		if err != nil {
			return nil, err
		}
		body = bytes.NewReader(content)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-Agent-Key", c.apiKey)
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	if payload != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	content, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		return nil, err
	}

	var data Response
	if len(bytes.TrimSpace(content)) > 0 {
		if err := json.Unmarshal(content, &data); err != nil {
			return nil, fmt.Errorf("control plane returned non-json response: %w", err)
		}
	} else {
		data = Response{}
	}

	if resp.StatusCode >= 400 {
		message := strings.TrimSpace(string(content))
		if value, ok := data["message"].(string); ok && strings.TrimSpace(value) != "" {
			message = strings.TrimSpace(value)
		}
		if message == "" {
			message = resp.Status
		}
		return nil, fmt.Errorf("control plane request failed: %s", message)
	}

	if success, ok := data["success"].(bool); ok && !success {
		if message, ok := data["message"].(string); ok && message != "" {
			return nil, fmt.Errorf("%s", message)
		}
		return nil, fmt.Errorf("control plane request failed")
	}
	return data, nil
}

func decodeJSONValue(input any, out any) error {
	content, err := json.Marshal(input)
	if err != nil {
		return err
	}
	return json.Unmarshal(content, out)
}
