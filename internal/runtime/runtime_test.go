package runtime

import (
	"context"
	"log/slog"
	"strings"
	"testing"

	"github.com/kaishzz/kepagent/internal/config"
)

func TestExtractRemoteBuildID(t *testing.T) {
	output := `
{
  "730": {
    "depots": {
      "branches": {
        "public": {
          "buildid": "29876543"
        }
      }
    }
  }
}
`
	if got := extractRemoteBuildID(output); got != "29876543" {
		t.Fatalf("unexpected buildid: %s", got)
	}
}

func TestInsertMetamodSearchPath(t *testing.T) {
	input := "GameInfo\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n\t\t\tGame\tcsgo\n\t\t}\n\t}\n}\n"
	updated, changed, err := insertMetamodSearchPath(input)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatal("expected change")
	}
	if updated == input {
		t.Fatal("expected updated content")
	}
	if updated != "GameInfo\n{\n\tFileSystem\n\t{\n\t\tSearchPaths\n\t\t{\n\t\t\tGame\tcsgo/addons/metamod\n\t\t\tGame\tcsgo\n\t\t}\n\t}\n}\n" {
		t.Fatalf("unexpected content:\n%s", updated)
	}
}

func TestSendRCONCommandUsesTargetHostAndTreatsMissingPasswordAsEmpty(t *testing.T) {
	rt := New(&config.Config{
		Servers: []config.Server{
			{
				Key: "server-a",
				Ports: []config.PortBinding{
					{HostPort: 28010, Protocol: "tcp"},
				},
			},
		},
	}, nil, slog.Default())
	logs := []string{}
	rt.SetLogEmitter(func(_ string, message string) {
		logs = append(logs, message)
	})

	result, err := rt.SendRCONCommand(context.Background(), "ALL", "status", []string{"server-a"}, []map[string]any{
		{"key": "server-a", "host": "catalog.local"},
	})
	if err != nil {
		t.Fatal(err)
	}
	if result["success"] != 0 {
		t.Fatalf("expected no success without password, got %v", result["success"])
	}
	rows, ok := result["results"].([]map[string]any)
	if !ok || len(rows) != 1 {
		t.Fatalf("unexpected results: %#v", result["results"])
	}
	if rows[0]["host"] != "catalog.local" {
		t.Fatalf("expected target host, got %#v", rows[0]["host"])
	}
	if rows[0]["error"] != "RCON password is empty" {
		t.Fatalf("expected empty password error, got %#v", rows[0]["error"])
	}
	if !strings.Contains(strings.Join(logs, "\n"), "catalog.local:28010") {
		t.Fatalf("expected log to include target host, got %#v", logs)
	}
}

func TestSendRCONCommandRequiresTargetHost(t *testing.T) {
	rt := New(&config.Config{
		Servers: []config.Server{
			{
				Key: "server-a",
				Ports: []config.PortBinding{
					{HostPort: 28010, Protocol: "tcp"},
				},
			},
		},
	}, nil, slog.Default())

	result, err := rt.SendRCONCommand(context.Background(), "ALL", "status", []string{"server-a"}, []map[string]any{
		{"key": "server-a", "password": "secret"},
	})
	if err != nil {
		t.Fatal(err)
	}
	rows, ok := result["results"].([]map[string]any)
	if !ok || len(rows) != 1 {
		t.Fatalf("unexpected results: %#v", result["results"])
	}
	if rows[0]["error"] != "RCON host is empty" {
		t.Fatalf("expected empty host error, got %#v", rows[0]["error"])
	}
}
