export type ColorMode = "light" | "dark" | "system";
type Palette = {
  background: string; panel: string; element: string; text: string; muted: string;
  primary: string; secondary: string; error: string; warning: string; success: string; info: string; border: string;
};

const channels = (hex: string) => [1, 3, 5].map((start) => parseInt(hex.slice(start, start + 2), 16));
function mix(a: string, b: string, amount: number) {
  const target = channels(b);
  return "#" + channels(a).map((value, index) => Math.round(value + (target[index] - value) * amount).toString(16).padStart(2, "0")).join("");
}
function luminance(hex: string) {
  return channels(hex).reduce((sum, value, index) => {
    const channel = value / 255;
    return sum + (channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4) * [0.2126, 0.7152, 0.0722][index];
  }, 0);
}
export function contrast(a: string, b: string) {
  return (Math.max(luminance(a), luminance(b)) + 0.05) / (Math.min(luminance(a), luminance(b)) + 0.05);
}
function readable(color: string, surfaces: string[], minimum = 4.5) {
  const target = luminance(surfaces[0]) < 0.5 ? "#ffffff" : "#000000";
  for (let step = 0; step <= 100; step++) {
    const candidate = mix(color, target, step / 100);
    if (surfaces.every((surface) => contrast(candidate, surface) >= minimum)) return candidate;
  }
  return target;
}
function tokens(palette: Palette) {
  const surfaces = [palette.background, palette.panel, palette.element];
  const foreground = readable(palette.text, surfaces);
  const primary = readable(palette.primary, surfaces);
  const accent = mix(palette.element, primary, 0.12);
  const destructive = readable(palette.error, surfaces);
  const added = mix(palette.panel, palette.success, 0.12);
  const removed = mix(palette.panel, palette.error, 0.12);
  return {
    "--background": palette.background, "--foreground": foreground,
    "--card": palette.panel, "--card-foreground": foreground,
    "--popover": palette.panel, "--popover-foreground": foreground,
    "--primary": primary, "--primary-foreground": readable(palette.background, [primary]),
    "--secondary": palette.element, "--secondary-foreground": foreground,
    "--muted": palette.element, "--muted-foreground": readable(palette.muted, [...surfaces, accent]),
    "--accent": accent, "--accent-foreground": readable(palette.text, [accent]),
    "--destructive": destructive, "--destructive-foreground": readable(palette.background, [destructive]),
    "--border": palette.border, "--input": palette.border, "--ring": readable(palette.primary, surfaces, 3),
    "--code-keyword": readable(palette.secondary, surfaces), "--code-string": readable(palette.success, surfaces),
    "--code-number": readable(palette.warning, surfaces), "--code-function": readable(palette.info, surfaces),
    "--code-added": readable(palette.success, [added]), "--code-added-background": added,
    "--code-removed": readable(palette.error, [removed]), "--code-removed-background": removed,
  };
}
export type ThemeTokens = ReturnType<typeof tokens>;

// Dark palettes are local snapshots of noodle-site's eight site themes; light palettes are matching web adaptations.
const importedThemes = [
  { id: "noodle", label: "Noodle",
    light: { background: "#f8f5ef", panel: "#fffdf8", element: "#ede7dc", text: "#302b26", muted: "#746c62", primary: "#167d75", secondary: "#966e14", error: "#b74535", warning: "#9b601f", success: "#527d37", info: "#356ca8", border: "#cfc5b7" },
    dark: { background: "#181714", panel: "#181714", element: "#24211d", text: "#f3efe7", muted: "#aaa39a", primary: "#59c9be", secondary: "#f2c65a", error: "#ef7b63", warning: "#f0a96b", success: "#9dcb82", info: "#89b4fa", border: "#3b3630" } },
  { id: "aura", label: "Aura",
    light: { background: "#f7f5fb", panel: "#ffffff", element: "#ece6f5", text: "#302841", muted: "#776887", primary: "#7747ce", secondary: "#a139a8", error: "#bd3c54", warning: "#94601c", success: "#217760", info: "#7747ce", border: "#d2c6e3" },
    dark: { background: "#0f0f0f", panel: "#15141b", element: "#282633", text: "#edecee", muted: "#6d6d6d", primary: "#a277ff", secondary: "#f694ff", error: "#ff6767", warning: "#ffca85", success: "#61ffca", info: "#a277ff", border: "#2d2d2d" } },
  { id: "carbonfox", label: "Carbonfox",
    light: { background: "#f4f6f9", panel: "#ffffff", element: "#e5eaf1", text: "#202a36", muted: "#627084", primary: "#006cae", secondary: "#345bb5", error: "#ae2858", warning: "#86680c", success: "#167541", info: "#345bb5", border: "#c1ccda" },
    dark: { background: "#161616", panel: "#1a1a1a", element: "#2a2a2a", text: "#f2f4f8", muted: "#7d848f", primary: "#33b1ff", secondary: "#78a9ff", error: "#ee5396", warning: "#f1c21b", success: "#25be6a", info: "#78a9ff", border: "#303030" } },
  { id: "catppuccin", label: "Catppuccin",
    light: { background: "#eff1f5", panel: "#e6e9ef", element: "#dce0e8", text: "#4c4f69", muted: "#6c6f85", primary: "#8839ef", secondary: "#1e66f5", error: "#d20f39", warning: "#df8e1d", success: "#40a02b", info: "#179299", border: "#bcc0cc" },
    dark: { background: "#1e1e2e", panel: "#181825", element: "#313244", text: "#cdd6f4", muted: "#6c7086", primary: "#cba6f7", secondary: "#89b4fa", error: "#f38ba8", warning: "#fab387", success: "#a6e3a1", info: "#89dceb", border: "#45475a" } },
  { id: "claude-code", label: "Claude Code",
    light: { background: "#faf8f5", panel: "#fffdfa", element: "#eee8df", text: "#332d29", muted: "#796e66", primary: "#a54b2f", secondary: "#6665a8", error: "#b23f3d", warning: "#946020", success: "#58703b", info: "#3e719e", border: "#d5c9bb" },
    dark: { background: "#1f1f1f", panel: "#1f1f1f", element: "#373737", text: "#ffffff", muted: "#a0a0a0", primary: "#da7756", secondary: "#b1b9f9", error: "#ef6f6c", warning: "#e0a458", success: "#7a8f5f", info: "#6a9bcc", border: "#505050" } },
  { id: "cobalt2", label: "Cobalt2",
    light: { background: "#f1f7fc", panel: "#ffffff", element: "#dfeaf4", text: "#193549", muted: "#57758d", primary: "#006ac7", secondary: "#7848ad", error: "#bd0065", warning: "#906b00", success: "#477d2c", info: "#a7600b", border: "#b6cfe3" },
    dark: { background: "#193549", panel: "#122738", element: "#1f4662", text: "#ffffff", muted: "#adb7c9", primary: "#0088ff", secondary: "#9a5feb", error: "#ff0088", warning: "#ffc600", success: "#9eff80", info: "#ff9d00", border: "#1f4662" } },
  { id: "dracula", label: "Dracula",
    light: { background: "#f8f7fc", panel: "#ffffff", element: "#eae7f2", text: "#282a36", muted: "#6a6585", primary: "#7947be", secondary: "#aa367f", error: "#bd303d", warning: "#807019", success: "#257b42", info: "#a36225", border: "#ccc6dc" },
    dark: { background: "#282a36", panel: "#21222c", element: "#44475a", text: "#f8f8f2", muted: "#6272a4", primary: "#bd93f9", secondary: "#ff79c6", error: "#ff5555", warning: "#f1fa8c", success: "#50fa7b", info: "#ffb86c", border: "#44475a" } },
  { id: "synthwave84", label: "Synthwave84",
    light: { background: "#faf5fc", panel: "#fffbff", element: "#eee1f3", text: "#36233f", muted: "#80668e", primary: "#007e83", secondary: "#b02c89", error: "#bc2941", warning: "#8b6a06", success: "#197b5b", info: "#a75313", border: "#d7bddf" },
    dark: { background: "#262335", panel: "#1e1a29", element: "#332a45", text: "#ffffff", muted: "#848bbd", primary: "#36f9f6", secondary: "#ff7edb", error: "#fe4450", warning: "#fede5d", success: "#72f1b8", info: "#ff8b39", border: "#495495" } },
] as const satisfies readonly { id: string; label: string; light: Palette; dark: Palette }[];

export const themes = [
  { id: "code-mortis", label: "Code Mortis", light: {
    "--background": "#ffffff", "--foreground": "#202c26", "--card": "#ffffff", "--card-foreground": "#202c26",
    "--popover": "#ffffff", "--popover-foreground": "#202c26", "--primary": "#245944", "--primary-foreground": "#ffffff",
    "--secondary": "#e9eee8", "--secondary-foreground": "#254533", "--muted": "#eef0ec", "--muted-foreground": "#606a62",
    "--accent": "#e1ebe2", "--accent-foreground": "#173e2a", "--destructive": "#b32d37", "--destructive-foreground": "#ffffff",
    "--border": "#dce1d9", "--input": "#c5cec3", "--ring": "#377c59",
    "--code-keyword": "#6639a6", "--code-string": "#245944", "--code-number": "#924808", "--code-function": "#125d8a",
    "--code-added": "#18472c", "--code-added-background": "#e5f1e7", "--code-removed": "#8c2530", "--code-removed-background": "#fbeaec",
  }, dark: {
    "--background": "#17201c", "--foreground": "#e8eee9", "--card": "#1e2923", "--card-foreground": "#e8eee9",
    "--popover": "#25322b", "--popover-foreground": "#e8eee9", "--primary": "#a2d1aa", "--primary-foreground": "#163021",
    "--secondary": "#293a2f", "--secondary-foreground": "#d0e6d3", "--muted": "#25312a", "--muted-foreground": "#a7b6ab",
    "--accent": "#304a39", "--accent-foreground": "#ddf0df", "--destructive": "#ff9b9b", "--destructive-foreground": "#17201c",
    "--border": "#35433a", "--input": "#53685a", "--ring": "#95c9a5",
    "--code-keyword": "#d9b8ff", "--code-string": "#a2d1aa", "--code-number": "#efc28d", "--code-function": "#9ad9f5",
    "--code-added": "#b9e4c4", "--code-added-background": "#243e2e", "--code-removed": "#ffc1c7", "--code-removed-background": "#4a292d",
  } },
  ...importedThemes.map(({ id, label, light, dark }) => ({ id, label, light: tokens(light), dark: tokens(dark) })),
] as const satisfies readonly { id: string; label: string; light: ThemeTokens; dark: ThemeTokens }[];
export type ThemeId = typeof themes[number]["id"];

export function readAppearance(): { mode: ColorMode; theme: ThemeId } {
  let mode: ColorMode = "system";
  let theme: ThemeId = "claude-code";
  try {
    const savedMode = localStorage.getItem("theme");
    if (savedMode === "light" || savedMode === "dark") mode = savedMode;
    const savedTheme = localStorage.getItem("color-theme");
    theme = themes.find((item) => item.id === savedTheme)?.id ?? theme;
  } catch { /* Appearance remains usable when storage is unavailable. */ }
  return { mode, theme };
}

export function applyAppearance(theme: ThemeId, dark: boolean) {
  const root = document.documentElement;
  const palette = themes.find((item) => item.id === theme)!;
  root.classList.toggle("dark", dark);
  root.dataset.colorTheme = theme;
  for (const [property, value] of Object.entries(palette[dark ? "dark" : "light"])) root.style.setProperty(property, value);
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", palette[dark ? "dark" : "light"]["--background"]);
}
