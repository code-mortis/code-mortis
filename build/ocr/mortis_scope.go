package tool

import (
	"encoding/json"
	"os"
	"path"
	"strings"
	"sync"

	"github.com/alibaba/open-code-review/internal/config/rules"
)

var mortisScope struct {
	sync.Once
	filter *rules.FileFilter
}

// Only Code Mortis review subprocesses set this. Scan coverage stays independent.
func mortisAllowed(name string) bool {
	file := os.Getenv("CM_OCR_REVIEW_RULES")
	if file == "" {
		return true
	}
	mortisScope.Do(func() {
		data, err := os.ReadFile(file)
		var policy rules.ProjectRule
		if err == nil && json.Unmarshal(data, &policy) == nil && len(policy.Exclude) > 0 {
			mortisScope.filter = &rules.FileFilter{Exclude: policy.Exclude}
		}
	})
	if mortisScope.filter == nil || name == "" || path.IsAbs(name) || path.Clean(name) != name || strings.ContainsAny(name, "\\\x00\r\n") {
		return false
	}
	for _, part := range strings.Split(name, "/") {
		if part == ".." {
			return false
		}
	}
	return !mortisScope.filter.IsUserExcluded(name)
}
