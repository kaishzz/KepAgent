package agent

import (
	"context"
	"strings"
	"sync"
	"time"

	"github.com/kaishzz/kepagent/internal/api"
)

type LiveLogger struct {
	mu            sync.Mutex
	emit          func(context.Context, []api.LogEntry) error
	batchSize     int
	flushInterval time.Duration
	lastFlush     time.Time
	buffer        []api.LogEntry
	messages      []string
}

func NewLiveLogger(emit func(context.Context, []api.LogEntry) error) *LiveLogger {
	return &LiveLogger{
		emit:          emit,
		batchSize:     10,
		flushInterval: 500 * time.Millisecond,
		lastFlush:     time.Now(),
	}
}

func (l *LiveLogger) Append(ctx context.Context, message string) {
	l.Emit(ctx, "info", message)
}

func (l *LiveLogger) Emit(ctx context.Context, level string, message string) {
	message = strings.TrimSpace(message)
	if message == "" {
		return
	}
	if strings.TrimSpace(level) == "" {
		level = "info"
	}

	l.mu.Lock()
	l.messages = append(l.messages, message)
	l.buffer = append(l.buffer, api.LogEntry{Level: level, Message: message})
	shouldFlush := len(l.buffer) >= l.batchSize || time.Since(l.lastFlush) >= l.flushInterval
	l.mu.Unlock()

	if shouldFlush {
		_ = l.Flush(ctx)
	}
}

func (l *LiveLogger) Flush(ctx context.Context) error {
	for {
		l.mu.Lock()
		if len(l.buffer) == 0 {
			l.lastFlush = time.Now()
			l.mu.Unlock()
			return nil
		}
		batchSize := min(200, len(l.buffer))
		batch := append([]api.LogEntry(nil), l.buffer[:batchSize]...)
		l.buffer = l.buffer[batchSize:]
		l.lastFlush = time.Now()
		l.mu.Unlock()

		if err := l.emit(ctx, batch); err != nil {
			return err
		}
	}
}

func (l *LiveLogger) Messages() []string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]string(nil), l.messages...)
}
