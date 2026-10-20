// SPDX-License-Identifier: MIT
package llm

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestCodeMortisResultDiagnostics(t *testing.T) {
	for _, arguments := range []string{`{"state":"DONE","result":{"explanation":"private-source-and-credential"}}`, `{}`, `{"state":"DONE"}`, `{"state":`, `{"state":"DONE","result":{}}<|end|>`} {
		t.Run(arguments, func(t *testing.T) {
			file := filepath.Join(t.TempDir(), "delivery.jsonl")
			if err := os.WriteFile(file, nil, 0600); err != nil {
				t.Fatal(err)
			}
			t.Setenv("CM_OCR_DIAGNOSTIC_KEY", strings.Repeat("ab", 32))
			t.Setenv("CM_OCR_DIAGNOSTIC_FILE", file)
			body, _ := json.Marshal(map[string]any{"private": "private-source-and-credential", "output": []any{
				map[string]any{"type": "function_call", "call_id": "secret-call-id", "name": "task_done", "arguments": arguments},
			}})
			recordResultDiagnostics(string(body))
			data, err := os.ReadFile(file)
			if err != nil {
				t.Fatal(err)
			}
			if strings.Contains(string(data), "private-source") || strings.Contains(string(data), "secret-call-id") || strings.Contains(string(data), strings.Repeat("ab", 32)) {
				t.Fatal("diagnostic leaked private content")
			}
			var result struct {
				Version int
				Calls   []resultCallDiagnostic
			}
			if err := json.Unmarshal(data, &result); err != nil {
				t.Fatal(err)
			}
			if result.Version != 1 || len(result.Calls) != 1 || result.Calls[0].Bytes != len(arguments) || len(result.Calls[0].Fingerprint) != 64 {
				t.Fatalf("incomplete diagnostic: %s", data)
			}
			if strings.Contains(arguments, `"result":`) && !result.Calls[0].Result {
				t.Fatal("valid result was lost")
			}
		})
	}
}

func TestCodeMortisResultDiagnosticsCannotFailTheRequest(t *testing.T) {
	t.Setenv("CM_OCR_DIAGNOSTIC_KEY", "invalid-private-key")
	t.Setenv("CM_OCR_DIAGNOSTIC_FILE", "/unavailable/diagnostics")
	recordResultDiagnostics(`{"output":[]}`)
	t.Setenv("CM_OCR_DIAGNOSTIC_KEY", strings.Repeat("ab", 32))
	recordResultDiagnostics(`not json`)
	recordResultDiagnostics(`{"output":[]}`)
}
