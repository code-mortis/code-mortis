// SPDX-License-Identifier: MIT
// Code Mortis observes the SDK's original JSON before OCR maps its tool calls.
package llm

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"strings"
)

type resultCallDiagnostic struct {
	Call         string `json:"call"`
	TaskDone     bool   `json:"task_done"`
	ArgumentType string `json:"argument_type"`
	Bytes        int    `json:"bytes"`
	Fingerprint  string `json:"fingerprint,omitempty"`
	JSONValid    bool   `json:"json_valid"`
	Done         bool   `json:"done"`
	Result       bool   `json:"result_object"`
}

func resultFingerprint(key []byte, value string) string {
	mac := hmac.New(sha256.New, key)
	mac.Write([]byte(value))
	return hex.EncodeToString(mac.Sum(nil))
}

// The sink receives an allowlist of keyed hashes, counts and booleans, never bodies or headers.
func recordResultDiagnostics(response string) {
	key, err := hex.DecodeString(os.Getenv("CM_OCR_DIAGNOSTIC_KEY"))
	if err != nil || len(key) != 32 || len(response) > 20_000_000 {
		return
	}
	var body struct {
		Output []struct {
			Type      string          `json:"type"`
			Name      string          `json:"name"`
			CallID    string          `json:"call_id"`
			Arguments json.RawMessage `json:"arguments"`
		} `json:"output"`
	}
	if json.Unmarshal([]byte(response), &body) != nil {
		return
	}
	calls := make([]resultCallDiagnostic, 0)
	for _, item := range body.Output {
		if item.Type != "function_call" {
			continue
		}
		if len(calls) == 16 {
			return // An incomplete capture must not be used to attribute a failure.
		}
		call := resultCallDiagnostic{Call: resultFingerprint(key, "call\x00"+item.CallID), TaskDone: item.Name == "task_done", ArgumentType: "non_string"}
		var arguments string
		if json.Unmarshal(item.Arguments, &arguments) == nil && string(item.Arguments) != "null" {
			call.ArgumentType = "string"
			call.Bytes = len(arguments)
			call.Fingerprint = resultFingerprint(key, "arguments\x00"+arguments)
			var value any
			call.JSONValid = json.Unmarshal([]byte(arguments), &value) == nil
			parsed := call.JSONValid
			if !call.JSONValid {
				if start := strings.IndexAny(arguments, "{["); start >= 0 {
					parsed = json.NewDecoder(strings.NewReader(arguments[start:])).Decode(&value) == nil
				}
			}
			if object, ok := value.(map[string]any); parsed && ok {
				call.Done = object["state"] == "DONE"
				_, call.Result = object["result"].(map[string]any)
			}
		}
		calls = append(calls, call)
	}
	record := struct {
		Version int                    `json:"version"`
		Attempt string                 `json:"attempt"`
		Calls   []resultCallDiagnostic `json:"calls"`
	}{Version: 1, Attempt: resultFingerprint(key, "attempt\x00code-mortis.delivery/v1"), Calls: calls}
	line, err := json.Marshal(record)
	if err != nil {
		return
	}
	file, err := os.OpenFile(os.Getenv("CM_OCR_DIAGNOSTIC_FILE"), os.O_WRONLY|os.O_APPEND, 0600)
	if err != nil {
		return
	}
	defer file.Close()
	if info, err := file.Stat(); err == nil && info.Mode().IsRegular() && info.Size()+int64(len(line))+1 <= 65536 {
		file.Write(append(line, '\n'))
	}
}
