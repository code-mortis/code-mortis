// SPDX-License-Identifier: MIT
package llm

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"regexp"
	"sort"
	"strings"
)

var mortisToken = regexp.MustCompile(`\b(gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]+)\b`)
var mortisBearer = regexp.MustCompile(`(?i)(bearer\s+)[\w.\-]+`)
var mortisUnsafeContext = errors.New("Code Mortis could not safely sanitize model context")

// Authentication configuration is separate from model content. This guard covers every adapter and turn.
func SanitizeMortisRequest(ctx context.Context, req ChatRequest) (ChatRequest, error) {
	path := os.Getenv("CM_OCR_REDACTION_FILE")
	if path == "" {
		return req, nil
	}
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 1048576 {
		return req, mortisUnsafeContext
	}
	data, err := os.ReadFile(path)
	var values []string
	if err != nil || json.Unmarshal(data, &values) != nil {
		return req, mortisUnsafeContext
	}
	sort.Slice(values, func(i, j int) bool { return len(values[i]) > len(values[j]) })
	redact := func(text string) string {
		for _, value := range values {
			if value == "" {
				continue
			}
			replacement := strings.Repeat("[redacted]\n", strings.Count(value, "\n")) + "[redacted]"
			text = strings.ReplaceAll(text, value, replacement)
			encoded, _ := json.Marshal(value)
			masked, _ := json.Marshal(replacement)
			text = strings.ReplaceAll(text, string(encoded[1:len(encoded)-1]), string(masked[1:len(masked)-1]))
		}
		text = mortisToken.ReplaceAllString(text, "[redacted]")
		return mortisBearer.ReplaceAllString(text, "${1}[redacted]")
	}
	containsKnown := func(text string) bool {
		for _, value := range values {
			if value == "" {
				continue
			}
			encoded, _ := json.Marshal(value)
			if strings.Contains(text, value) || strings.Contains(text, string(encoded[1:len(encoded)-1])) {
				return true
			}
		}
		return false
	}
	var clone func(reflect.Value, bool) (reflect.Value, error)
	clone = func(v reflect.Value, identifier bool) (reflect.Value, error) {
		if err := ctx.Err(); err != nil {
			return v, err
		}
		out := reflect.New(v.Type()).Elem()
		switch v.Kind() {
		case reflect.String:
			clean := redact(v.String())
			if identifier && clean != v.String() {
				return v, mortisUnsafeContext
			}
			out.SetString(clean)
		case reflect.Interface:
			if !v.IsNil() {
				child, err := clone(v.Elem(), identifier)
				if err != nil {
					return v, err
				}
				out.Set(child)
			}
		case reflect.Slice:
			if v.Type().Elem().Kind() == reflect.Uint8 {
				// Opaque provider signatures and raw definitions must retain their exact bytes.
				if redact(string(v.Bytes())) != string(v.Bytes()) {
					return v, mortisUnsafeContext
				}
				return v, nil
			}
			if !v.IsNil() {
				out = reflect.MakeSlice(v.Type(), v.Len(), v.Len())
				for i := 0; i < v.Len(); i++ {
					child, err := clone(v.Index(i), identifier)
					if err != nil {
						return v, err
					}
					out.Index(i).Set(child)
				}
			}
		case reflect.Map:
			if !v.IsNil() {
				out = reflect.MakeMapWithSize(v.Type(), v.Len())
				iter := v.MapRange()
				for iter.Next() {
					key, err := clone(iter.Key(), true)
					if err != nil {
						return v, err
					}
					child, err := clone(iter.Value(), identifier)
					if err != nil {
						return v, err
					}
					out.SetMapIndex(key, child)
				}
			}
		case reflect.Struct:
			out.Set(v)
			for i := 0; i < v.NumField(); i++ {
				field := v.Type().Field(i)
				if field.PkgPath != "" {
					continue
				}
				if field.Name == "Native" || field.Name == "ExtraContent" {
					encoded, err := json.Marshal(v.Field(i).Interface())
					if err != nil || containsKnown(string(encoded)) {
						return v, mortisUnsafeContext
					}
					continue
				}
				structural := identifier || field.Name == "Model" || field.Name == "Role" || field.Name == "ID" || field.Name == "Name" || field.Name == "Type" || field.Name == "ToolCallID" || field.Name == "ToolUseID" || field.Name == "SessionID"
				child, err := clone(v.Field(i), structural)
				if err != nil {
					return v, err
				}
				out.Field(i).Set(child)
			}
		default:
			out.Set(v)
		}
		return out, nil
	}
	result, err := clone(reflect.ValueOf(req), false)
	if err != nil {
		return req, err
	}
	return result.Interface().(ChatRequest), nil
}
