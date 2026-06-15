package runtime

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"strings"
	"testing"
	"time"

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

func TestRunProcessWithLiveOutputEmitsLines(t *testing.T) {
	rt := New(&config.Config{}, nil, slog.Default())
	logs := []string{}
	rt.SetLogEmitter(func(level string, message string) {
		logs = append(logs, level+":"+message)
	})

	output, err := rt.runProcessWithLiveOutput(
		context.Background(),
		5*time.Second,
		os.Args[0],
		"-test.run=TestHelperProcess",
		"--",
		"stream-output",
	)
	if err != nil {
		t.Fatal(err)
	}

	for _, expected := range []string{
		"info:stdout line",
		"info:progress 50",
		"info:progress 100",
		"error:stderr line",
	} {
		if !strings.Contains(strings.Join(logs, "\n"), expected) {
			t.Fatalf("expected log %q, got %#v", expected, logs)
		}
	}
	if !strings.Contains(output, "stdout line") || !strings.Contains(output, "stderr line") {
		t.Fatalf("unexpected output: %q", output)
	}
}

func TestSteamcmdValidateStopConditionTreatsUnknownAfterVerifyAsComplete(t *testing.T) {
	rt := New(&config.Config{}, nil, slog.Default())
	logs := []string{}
	rt.SetLogEmitter(func(level string, message string) {
		logs = append(logs, level+":"+message)
	})
	stopCondition := rt.buildSteamcmdValidateStopCondition()

	if stopCondition("info", "Update state (0x0) unknown, progress: 0.00 (0 / 0)") {
		t.Fatal("unknown state without near-complete verify should not stop")
	}
	if stopCondition("info", "Update state (0x5) verifying install, progress: 99.65 (65704421785 / 65933511279)") {
		t.Fatal("verify progress should not stop immediately")
	}
	if !stopCondition("info", "Update state (0x0) unknown, progress: 0.00 (0 / 0)") {
		t.Fatal("unknown state after near-complete verify should stop")
	}
	if !strings.Contains(strings.Join(logs, "\n"), "Detected steamcmd terminal unknown state after verify") {
		t.Fatalf("expected completion log, got %#v", logs)
	}
}

func TestRunProcessWithLiveOutputTreatsStopConditionAsSuccess(t *testing.T) {
	rt := New(&config.Config{}, nil, slog.Default())
	logs := []string{}
	rt.SetLogEmitter(func(level string, message string) {
		logs = append(logs, level+":"+message)
	})

	output, err := rt.runProcessWithLiveOutputUntil(
		context.Background(),
		5*time.Second,
		rt.buildSteamcmdValidateStopCondition(),
		os.Args[0],
		"-test.run=TestHelperProcess",
		"--",
		"stop-after-success",
	)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output, "Success! App '730' fully installed.") {
		t.Fatalf("unexpected output: %q", output)
	}
	if !strings.Contains(strings.Join(logs, "\n"), "Detected steamcmd completion marker") {
		t.Fatalf("expected completion log, got %#v", logs)
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

func TestHelperProcess(t *testing.T) {
	for index, arg := range os.Args {
		if arg == "--" && index+1 < len(os.Args) && os.Args[index+1] == "stream-output" {
			fmt.Fprintln(os.Stdout, "stdout line")
			fmt.Fprint(os.Stdout, "progress 50\rprogress 100\n")
			fmt.Fprintln(os.Stderr, "stderr line")
			os.Exit(0)
		}
		if arg == "--" && index+1 < len(os.Args) && os.Args[index+1] == "stop-after-success" {
			fmt.Fprintln(os.Stdout, "Success! App '730' fully installed.")
			time.Sleep(time.Minute)
			os.Exit(1)
		}
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
