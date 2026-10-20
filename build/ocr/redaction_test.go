package llm

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
)

func TestCodeMortisRedaction(t *testing.T) {
	secret := "synthetic-雪\ncredential"
	path := filepath.Join(t.TempDir(), "redaction.json")
	data, _ := json.Marshal([]string{secret})
	if err := os.WriteFile(path, data, 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CM_OCR_REDACTION_FILE", path)
	original := ChatRequest{Model: "test", SessionID: "stable", Messages: []Message{
		{Role: "user", Content: secret},
		{Role: "tool", Content: []ContentBlock{{Type: "text", Text: secret}}},
		{Role: "assistant", ToolCalls: []ToolCall{{ID: "id", Function: FunctionCall{Name: "task_done", Arguments: string(data)}}}},
	}, Tools: []ToolDef{{Type: "function", Function: FunctionDef{Name: "task_done", Description: secret, Parameters: map[string]any{"type": "object"}}}}}
	clean, err := SanitizeMortisRequest(context.Background(), original)
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(clean)
	escaped, _ := json.Marshal(secret)
	if strings.Contains(string(encoded), string(escaped[1:len(escaped)-1])) {
		t.Fatal("secret survived")
	}
	if original.Messages[0].Content != secret || clean.SessionID != "stable" {
		t.Fatal("mutated original or session")
	}
	blocks, ok := clean.Messages[1].Content.([]ContentBlock)
	if !ok || strings.Count(blocks[0].Text, "\n") != 1 {
		t.Fatal("lost content type or line mapping")
	}
	second, err := SanitizeMortisRequest(context.Background(), clean)
	if err != nil || !reflect.DeepEqual(clean, second) {
		t.Fatal("sanitization is not idempotent")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := SanitizeMortisRequest(ctx, original); err == nil {
		t.Fatal("ignored cancellation")
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := SanitizeMortisRequest(context.Background(), original); err == nil {
		t.Fatal("accepted public secret file")
	}
}

func TestCodeMortisRedactionIdentifiersAndOpaqueState(t *testing.T) {
	path := filepath.Join(t.TempDir(), "redaction.json")
	if err := os.WriteFile(path, []byte(`["synthetic-secret"]`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CM_OCR_REDACTION_FILE", path)
	for _, req := range []ChatRequest{
		{Model: "synthetic-secret"},
		{Messages: []Message{{Role: "assistant", ToolCalls: []ToolCall{{ExtraContent: json.RawMessage(`{"opaque":"synthetic-secret"}`)}}}}},
	} {
		if _, err := SanitizeMortisRequest(context.Background(), req); err == nil {
			t.Fatal("unsafe structural data accepted")
		}
	}
}

func TestCodeMortisPreservesOpaqueProviderReplay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "redaction.json")
	if err := os.WriteFile(path, []byte(`["known-credential"]`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CM_OCR_REDACTION_FILE", path)
	original := ChatRequest{Messages: []Message{{Role: "assistant", Native: NativeTurn{Family: "openai-chat-completions", Payload: ReasoningPayload("sk-opaque-provider-state")}, ToolCalls: []ToolCall{{ExtraContent: json.RawMessage(`{"signature":"sk-opaque-provider-signature"}`)}}}}}
	clean, err := SanitizeMortisRequest(context.Background(), original)
	if err != nil || !reflect.DeepEqual(clean, original) {
		t.Fatal("changed opaque provider state", err)
	}
	original.Messages[0].Native.Payload = ReasoningPayload("known-credential")
	if _, err := SanitizeMortisRequest(context.Background(), original); err == nil {
		t.Fatal("replayed known credential")
	}
}
