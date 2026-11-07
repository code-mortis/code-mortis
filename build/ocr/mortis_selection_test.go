package agent

import (
	"testing"

	"github.com/alibaba/open-code-review/internal/config/rules"
	"github.com/alibaba/open-code-review/internal/model"
)

func TestCodeMortisExcludedRenameCannotBeIncluded(t *testing.T) {
	agent := New(Args{FileFilter: &rules.FileFilter{Exclude: []string{"**/*.svg"}, Include: []string{"**/*"}}})
	for _, diff := range []model.Diff{{OldPath: "drawing.SVG", NewPath: "component.ts"}, {OldPath: "app.ts", NewPath: "drawing.svg"}} {
		if agent.whyExcluded(diff) != ExcludeUserRule { t.Fatal("excluded rename was admitted") }
	}
}
