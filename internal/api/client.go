package api

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

type rateLimitedReader struct {
	source     io.Reader
	bytesPerSec int64
	allowance  int64
	windowStart time.Time
}

func newRateLimitedReader(source io.Reader, limitMbps int) io.Reader {
	if limitMbps <= 0 {
		return source
	}

	bytesPerSec := int64(limitMbps) * 1024 * 1024 / 8
	if bytesPerSec <= 0 {
		return source
	}

	return &rateLimitedReader{
		source:      source,
		bytesPerSec: bytesPerSec,
		windowStart: time.Now(),
	}
}

func (r *rateLimitedReader) Read(p []byte) (int, error) {
	if len(p) == 0 {
		return r.source.Read(p)
	}

	if time.Since(r.windowStart) >= time.Second {
		r.windowStart = time.Now()
		r.allowance = 0
	}

	remaining := r.bytesPerSec - r.allowance
	if remaining <= 0 {
		sleepFor := time.Second - time.Since(r.windowStart)
		if sleepFor > 0 {
			time.Sleep(sleepFor)
		}
		r.windowStart = time.Now()
		r.allowance = 0
		remaining = r.bytesPerSec
	}

	if int64(len(p)) > remaining {
		p = p[:remaining]
	}

	n, err := r.source.Read(p)
	r.allowance += int64(n)
	return n, err
}

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

func (c *Client) DownloadReplayAsset(ctx context.Context, assetID, destinationPath string, limitMbps int) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+"/agent/api/replay-assets/"+url.PathEscape(assetID), nil)
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/octet-stream")
	req.Header.Set("X-Agent-Key", c.apiKey)
	req.Header.Set("Authorization", "Bearer "+c.apiKey)

	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		content, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
		return fmt.Errorf("control plane request failed: %s", strings.TrimSpace(string(content)))
	}

	file, err := os.Create(destinationPath)
	if err != nil {
		return err
	}
	defer file.Close()

	_, err = io.Copy(file, newRateLimitedReader(resp.Body, limitMbps))
	return err
}

func (c *Client) UploadReplayAsset(ctx context.Context, assetID, filePath, fileName, mimeType, sha256 string, sizeBytes int64, limitMbps int) error {
	file, err := os.Open(filePath)
	if err != nil {
		return err
	}
	defer file.Close()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/agent/api/replay-assets/"+url.PathEscape(assetID), newRateLimitedReader(file, limitMbps))
	if err != nil {
		return err
	}
	req.Header.Set("Accept", "application/json")
	req.Header.Set("X-Agent-Key", c.apiKey)
	req.Header.Set("Authorization", "Bearer "+c.apiKey)
	req.Header.Set("Content-Type", "application/octet-stream")
	req.Header.Set("X-Replay-File-Name", fileName)
	req.Header.Set("X-Replay-Mime-Type", mimeType)
	req.Header.Set("X-Replay-Sha256", sha256)
	req.Header.Set("X-Replay-Size-Bytes", fmt.Sprint(sizeBytes))

	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	content, err := io.ReadAll(io.LimitReader(resp.Body, 4*1024*1024))
	if err != nil {
		return err
	}

	var data Response
	if len(bytes.TrimSpace(content)) > 0 {
		if err := json.Unmarshal(content, &data); err != nil {
			return fmt.Errorf("control plane returned non-json response: %w", err)
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
		return fmt.Errorf("control plane request failed: %s", message)
	}

	return nil
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
