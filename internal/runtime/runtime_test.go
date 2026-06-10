package runtime

import "testing"

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
