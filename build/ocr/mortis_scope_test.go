package tool

import (
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

func TestCodeMortisReviewEvidenceScope(t *testing.T) {
	root := t.TempDir()
	rules := filepath.Join(t.TempDir(), "rules.json")
	if err := os.WriteFile(rules, []byte(`{"exclude":["**/*.svg","**/private/**"],"include":["**/*"]}`), 0600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("CM_OCR_REVIEW_RULES", rules)
	for _, name := range []string{"art.SVG", "page.mdx", "tricky:1:art.svg"} {
		if err := os.WriteFile(filepath.Join(root, name), []byte("scope-needle\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	cmd := exec.Command("git", "init", "-q", root)
	if err := cmd.Run(); err != nil {
		t.Fatal(err)
	}
	fr := &FileReader{RepoDir: root}
	ctx := context.Background()
	if _, err := fr.Read(ctx, "art.SVG"); err == nil {
		t.Fatal("excluded full read")
	}
	if _, _, err := fr.ReadLines(ctx, "art.SVG", 1, 10); err == nil {
		t.Fatal("excluded line read")
	}
	if _, err := fr.Read(ctx, "page.mdx"); err != nil {
		t.Fatal(err)
	}
	diff, err := NewFileReadDiff(NewDiffMap(map[string]string{"art.SVG": "hidden", "page.mdx": "visible"})).Execute(ctx, map[string]any{"path_array": []any{"art.SVG", "page.mdx"}})
	if err != nil || strings.Contains(diff, "hidden") || !strings.Contains(diff, "visible") {
		t.Fatal(diff, err)
	}
	result, err := NewCodeSearch(fr).Execute(ctx, map[string]any{"search_text": "scope-needle"})
	if err != nil || strings.Contains(result, "art") || !strings.Contains(result, "page.mdx") {
		t.Fatal(result, err)
	}
	result, err = NewFileFind(fr).Execute(ctx, map[string]any{"query_name": "art"})
	if err != nil || strings.Contains(result, "art.SVG") {
		t.Fatal(result, err)
	}
}
