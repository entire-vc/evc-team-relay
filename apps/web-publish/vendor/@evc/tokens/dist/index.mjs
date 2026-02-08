// src/colors.ts
function hexToRgb(hex) {
  const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
  if (!result) throw new Error(`Invalid hex color: ${hex}`);
  return {
    r: parseInt(result[1], 16),
    g: parseInt(result[2], 16),
    b: parseInt(result[3], 16)
  };
}
function hexToRgbString(hex) {
  const { r, g, b } = hexToRgb(hex);
  return `${r} ${g} ${b}`;
}
var entireColors = {
  primary: "#525769",
  primaryForeground: "#FFFFFF",
  secondary: "#DDE1E6",
  secondaryForeground: "#1A1A1A",
  // Neutrals
  background: "#FFFFFF",
  foreground: "#1A1A1A",
  muted: "#F4F4F5",
  mutedForeground: "#71717A",
  // UI Elements
  card: "#FFFFFF",
  cardForeground: "#1A1A1A",
  border: "#E4E4E7",
  input: "#E4E4E7",
  ring: "#525769",
  // Semantic
  destructive: "#EF4444",
  destructiveForeground: "#FFFFFF",
  success: "#22C55E",
  successForeground: "#FFFFFF",
  warning: "#F59E0B",
  warningForeground: "#FFFFFF",
  // Accent (same as primary for base theme)
  accent: "#525769",
  accentForeground: "#FFFFFF"
};
var sparkColors = {
  primary: "#FF6A3D",
  primaryForeground: "#FFFFFF",
  secondary: "#323643",
  secondaryForeground: "#FFFFFF",
  // Neutrals (dark theme base)
  background: "#1A1A1A",
  foreground: "#FAFAFA",
  muted: "#27272A",
  mutedForeground: "#A1A1AA",
  // UI Elements
  card: "#27272A",
  cardForeground: "#FAFAFA",
  border: "#3F3F46",
  input: "#3F3F46",
  ring: "#FF6A3D",
  // Semantic
  destructive: "#DC2626",
  destructiveForeground: "#FFFFFF",
  success: "#22C55E",
  successForeground: "#FFFFFF",
  warning: "#FFC947",
  // Brand yellow
  warningForeground: "#1A1A1A",
  // Accent (yellow fire)
  accent: "#FFC947",
  accentForeground: "#1A1A1A"
};
var playgroundColors = {
  primary: "#3D8BFF",
  primaryForeground: "#FFFFFF",
  secondary: "#AEB8C2",
  secondaryForeground: "#1A1A1A",
  // Neutrals
  background: "#FFFFFF",
  foreground: "#1A1A1A",
  muted: "#F1F5F9",
  mutedForeground: "#64748B",
  // UI Elements
  card: "#FFFFFF",
  cardForeground: "#1A1A1A",
  border: "#E2E8F0",
  input: "#E2E8F0",
  ring: "#3D8BFF",
  // Semantic
  destructive: "#EF4444",
  destructiveForeground: "#FFFFFF",
  success: "#22C55E",
  successForeground: "#FFFFFF",
  warning: "#F59E0B",
  warningForeground: "#FFFFFF",
  // Accent (teal/cyan)
  accent: "#33D6C9",
  accentForeground: "#1A1A1A"
};
var teamRelayColors = {
  primary: "#6366F1",
  // Indigo
  primaryForeground: "#FFFFFF",
  secondary: "#E0E7FF",
  secondaryForeground: "#3730A3",
  // Neutrals
  background: "#FFFFFF",
  foreground: "#1E1B4B",
  muted: "#F5F3FF",
  mutedForeground: "#6B7280",
  // UI Elements
  card: "#FFFFFF",
  cardForeground: "#1E1B4B",
  border: "#E5E7EB",
  input: "#E5E7EB",
  ring: "#6366F1",
  // Semantic
  destructive: "#EF4444",
  destructiveForeground: "#FFFFFF",
  success: "#10B981",
  successForeground: "#FFFFFF",
  warning: "#F59E0B",
  warningForeground: "#FFFFFF",
  // Accent
  accent: "#8B5CF6",
  // Purple
  accentForeground: "#FFFFFF"
};
var themes = {
  entire: entireColors,
  spark: sparkColors,
  playground: playgroundColors,
  "team-relay": teamRelayColors
};

// src/typography.ts
var fontFamily = {
  sans: ["Inter", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
  mono: ["JetBrains Mono", "Fira Code", "Monaco", "Consolas", "monospace"]
};
var fontSize = {
  xs: ["0.75rem", { lineHeight: "1rem" }],
  sm: ["0.875rem", { lineHeight: "1.25rem" }],
  base: ["1rem", { lineHeight: "1.5rem" }],
  lg: ["1.125rem", { lineHeight: "1.75rem" }],
  xl: ["1.25rem", { lineHeight: "1.75rem" }],
  "2xl": ["1.5rem", { lineHeight: "2rem" }],
  "3xl": ["1.875rem", { lineHeight: "2.25rem" }],
  "4xl": ["2.25rem", { lineHeight: "2.5rem" }],
  "5xl": ["3rem", { lineHeight: "1" }],
  "6xl": ["3.75rem", { lineHeight: "1" }]
};
var fontWeight = {
  normal: "400",
  medium: "500",
  semibold: "600",
  bold: "700"
};
var letterSpacing = {
  tighter: "-0.05em",
  tight: "-0.025em",
  normal: "0em",
  wide: "0.025em",
  wider: "0.05em"
};

// src/spacing.ts
var spacing = {
  px: "1px",
  0: "0",
  0.5: "0.125rem",
  // 2px
  1: "0.25rem",
  // 4px
  1.5: "0.375rem",
  // 6px
  2: "0.5rem",
  // 8px
  2.5: "0.625rem",
  // 10px
  3: "0.75rem",
  // 12px
  3.5: "0.875rem",
  // 14px
  4: "1rem",
  // 16px
  5: "1.25rem",
  // 20px
  6: "1.5rem",
  // 24px
  7: "1.75rem",
  // 28px
  8: "2rem",
  // 32px
  9: "2.25rem",
  // 36px
  10: "2.5rem",
  // 40px
  11: "2.75rem",
  // 44px
  12: "3rem",
  // 48px
  14: "3.5rem",
  // 56px
  16: "4rem",
  // 64px
  20: "5rem",
  // 80px
  24: "6rem",
  // 96px
  28: "7rem",
  // 112px
  32: "8rem",
  // 128px
  36: "9rem",
  // 144px
  40: "10rem",
  // 160px
  44: "11rem",
  // 176px
  48: "12rem",
  // 192px
  52: "13rem",
  // 208px
  56: "14rem",
  // 224px
  60: "15rem",
  // 240px
  64: "16rem",
  // 256px
  72: "18rem",
  // 288px
  80: "20rem",
  // 320px
  96: "24rem"
  // 384px
};
var borderRadius = {
  none: "0",
  sm: "0.125rem",
  // 2px
  DEFAULT: "0.375rem",
  // 6px - slightly rounded
  md: "0.5rem",
  // 8px
  lg: "0.75rem",
  // 12px
  xl: "1rem",
  // 16px
  "2xl": "1.5rem",
  // 24px
  "3xl": "2rem",
  // 32px
  full: "9999px"
};
var borderWidth = {
  DEFAULT: "1px",
  0: "0",
  2: "2px",
  4: "4px",
  8: "8px"
};

// src/effects.ts
var boxShadow = {
  sm: "0 1px 2px 0 rgb(0 0 0 / 0.05)",
  DEFAULT: "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)",
  md: "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)",
  lg: "0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)",
  xl: "0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)",
  "2xl": "0 25px 50px -12px rgb(0 0 0 / 0.25)",
  inner: "inset 0 2px 4px 0 rgb(0 0 0 / 0.05)",
  none: "none"
};
var animation = {
  none: "none",
  spin: "spin 1s linear infinite",
  ping: "ping 1s cubic-bezier(0, 0, 0.2, 1) infinite",
  pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
  bounce: "bounce 1s infinite",
  // shadcn animations
  "accordion-down": "accordion-down 0.2s ease-out",
  "accordion-up": "accordion-up 0.2s ease-out",
  "fade-in": "fade-in 0.2s ease-out",
  "fade-out": "fade-out 0.2s ease-out",
  "slide-in-from-top": "slide-in-from-top 0.2s ease-out",
  "slide-in-from-bottom": "slide-in-from-bottom 0.2s ease-out",
  "slide-in-from-left": "slide-in-from-left 0.2s ease-out",
  "slide-in-from-right": "slide-in-from-right 0.2s ease-out",
  "caret-blink": "caret-blink 1.25s ease-out infinite"
};
var keyframes = {
  "accordion-down": {
    from: { height: "0" },
    to: { height: "var(--radix-accordion-content-height)" }
  },
  "accordion-up": {
    from: { height: "var(--radix-accordion-content-height)" },
    to: { height: "0" }
  },
  "fade-in": {
    from: { opacity: "0" },
    to: { opacity: "1" }
  },
  "fade-out": {
    from: { opacity: "1" },
    to: { opacity: "0" }
  },
  "slide-in-from-top": {
    from: { transform: "translateY(-100%)" },
    to: { transform: "translateY(0)" }
  },
  "slide-in-from-bottom": {
    from: { transform: "translateY(100%)" },
    to: { transform: "translateY(0)" }
  },
  "slide-in-from-left": {
    from: { transform: "translateX(-100%)" },
    to: { transform: "translateX(0)" }
  },
  "slide-in-from-right": {
    from: { transform: "translateX(100%)" },
    to: { transform: "translateX(0)" }
  },
  "caret-blink": {
    "0%,70%,100%": { opacity: "1" },
    "20%,50%": { opacity: "0" }
  }
};
var transitionDuration = {
  75: "75ms",
  100: "100ms",
  150: "150ms",
  200: "200ms",
  300: "300ms",
  500: "500ms",
  700: "700ms",
  1e3: "1000ms"
};
var transitionTimingFunction = {
  DEFAULT: "cubic-bezier(0.4, 0, 0.2, 1)",
  linear: "linear",
  in: "cubic-bezier(0.4, 0, 1, 1)",
  out: "cubic-bezier(0, 0, 0.2, 1)",
  "in-out": "cubic-bezier(0.4, 0, 0.2, 1)"
};
export {
  animation,
  borderRadius,
  borderWidth,
  boxShadow,
  entireColors,
  fontFamily,
  fontSize,
  fontWeight,
  hexToRgb,
  hexToRgbString,
  keyframes,
  letterSpacing,
  playgroundColors,
  spacing,
  sparkColors,
  teamRelayColors,
  themes,
  transitionDuration,
  transitionTimingFunction
};
