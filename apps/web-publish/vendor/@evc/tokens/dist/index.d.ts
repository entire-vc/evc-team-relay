/**
 * Entire VC Color Palette
 *
 * Base brand colors and theme-specific variations
 */
declare function hexToRgb(hex: string): {
    r: number;
    g: number;
    b: number;
};
declare function hexToRgbString(hex: string): string;
declare const entireColors: {
    readonly primary: "#525769";
    readonly primaryForeground: "#FFFFFF";
    readonly secondary: "#DDE1E6";
    readonly secondaryForeground: "#1A1A1A";
    readonly background: "#FFFFFF";
    readonly foreground: "#1A1A1A";
    readonly muted: "#F4F4F5";
    readonly mutedForeground: "#71717A";
    readonly card: "#FFFFFF";
    readonly cardForeground: "#1A1A1A";
    readonly border: "#E4E4E7";
    readonly input: "#E4E4E7";
    readonly ring: "#525769";
    readonly destructive: "#EF4444";
    readonly destructiveForeground: "#FFFFFF";
    readonly success: "#22C55E";
    readonly successForeground: "#FFFFFF";
    readonly warning: "#F59E0B";
    readonly warningForeground: "#FFFFFF";
    readonly accent: "#525769";
    readonly accentForeground: "#FFFFFF";
};
declare const sparkColors: {
    readonly primary: "#FF6A3D";
    readonly primaryForeground: "#FFFFFF";
    readonly secondary: "#323643";
    readonly secondaryForeground: "#FFFFFF";
    readonly background: "#1A1A1A";
    readonly foreground: "#FAFAFA";
    readonly muted: "#27272A";
    readonly mutedForeground: "#A1A1AA";
    readonly card: "#27272A";
    readonly cardForeground: "#FAFAFA";
    readonly border: "#3F3F46";
    readonly input: "#3F3F46";
    readonly ring: "#FF6A3D";
    readonly destructive: "#DC2626";
    readonly destructiveForeground: "#FFFFFF";
    readonly success: "#22C55E";
    readonly successForeground: "#FFFFFF";
    readonly warning: "#FFC947";
    readonly warningForeground: "#1A1A1A";
    readonly accent: "#FFC947";
    readonly accentForeground: "#1A1A1A";
};
declare const playgroundColors: {
    readonly primary: "#3D8BFF";
    readonly primaryForeground: "#FFFFFF";
    readonly secondary: "#AEB8C2";
    readonly secondaryForeground: "#1A1A1A";
    readonly background: "#FFFFFF";
    readonly foreground: "#1A1A1A";
    readonly muted: "#F1F5F9";
    readonly mutedForeground: "#64748B";
    readonly card: "#FFFFFF";
    readonly cardForeground: "#1A1A1A";
    readonly border: "#E2E8F0";
    readonly input: "#E2E8F0";
    readonly ring: "#3D8BFF";
    readonly destructive: "#EF4444";
    readonly destructiveForeground: "#FFFFFF";
    readonly success: "#22C55E";
    readonly successForeground: "#FFFFFF";
    readonly warning: "#F59E0B";
    readonly warningForeground: "#FFFFFF";
    readonly accent: "#33D6C9";
    readonly accentForeground: "#1A1A1A";
};
declare const teamRelayColors: {
    readonly primary: "#6366F1";
    readonly primaryForeground: "#FFFFFF";
    readonly secondary: "#E0E7FF";
    readonly secondaryForeground: "#3730A3";
    readonly background: "#FFFFFF";
    readonly foreground: "#1E1B4B";
    readonly muted: "#F5F3FF";
    readonly mutedForeground: "#6B7280";
    readonly card: "#FFFFFF";
    readonly cardForeground: "#1E1B4B";
    readonly border: "#E5E7EB";
    readonly input: "#E5E7EB";
    readonly ring: "#6366F1";
    readonly destructive: "#EF4444";
    readonly destructiveForeground: "#FFFFFF";
    readonly success: "#10B981";
    readonly successForeground: "#FFFFFF";
    readonly warning: "#F59E0B";
    readonly warningForeground: "#FFFFFF";
    readonly accent: "#8B5CF6";
    readonly accentForeground: "#FFFFFF";
};
type ThemeColors = typeof entireColors;
declare const themes: {
    readonly entire: {
        readonly primary: "#525769";
        readonly primaryForeground: "#FFFFFF";
        readonly secondary: "#DDE1E6";
        readonly secondaryForeground: "#1A1A1A";
        readonly background: "#FFFFFF";
        readonly foreground: "#1A1A1A";
        readonly muted: "#F4F4F5";
        readonly mutedForeground: "#71717A";
        readonly card: "#FFFFFF";
        readonly cardForeground: "#1A1A1A";
        readonly border: "#E4E4E7";
        readonly input: "#E4E4E7";
        readonly ring: "#525769";
        readonly destructive: "#EF4444";
        readonly destructiveForeground: "#FFFFFF";
        readonly success: "#22C55E";
        readonly successForeground: "#FFFFFF";
        readonly warning: "#F59E0B";
        readonly warningForeground: "#FFFFFF";
        readonly accent: "#525769";
        readonly accentForeground: "#FFFFFF";
    };
    readonly spark: {
        readonly primary: "#FF6A3D";
        readonly primaryForeground: "#FFFFFF";
        readonly secondary: "#323643";
        readonly secondaryForeground: "#FFFFFF";
        readonly background: "#1A1A1A";
        readonly foreground: "#FAFAFA";
        readonly muted: "#27272A";
        readonly mutedForeground: "#A1A1AA";
        readonly card: "#27272A";
        readonly cardForeground: "#FAFAFA";
        readonly border: "#3F3F46";
        readonly input: "#3F3F46";
        readonly ring: "#FF6A3D";
        readonly destructive: "#DC2626";
        readonly destructiveForeground: "#FFFFFF";
        readonly success: "#22C55E";
        readonly successForeground: "#FFFFFF";
        readonly warning: "#FFC947";
        readonly warningForeground: "#1A1A1A";
        readonly accent: "#FFC947";
        readonly accentForeground: "#1A1A1A";
    };
    readonly playground: {
        readonly primary: "#3D8BFF";
        readonly primaryForeground: "#FFFFFF";
        readonly secondary: "#AEB8C2";
        readonly secondaryForeground: "#1A1A1A";
        readonly background: "#FFFFFF";
        readonly foreground: "#1A1A1A";
        readonly muted: "#F1F5F9";
        readonly mutedForeground: "#64748B";
        readonly card: "#FFFFFF";
        readonly cardForeground: "#1A1A1A";
        readonly border: "#E2E8F0";
        readonly input: "#E2E8F0";
        readonly ring: "#3D8BFF";
        readonly destructive: "#EF4444";
        readonly destructiveForeground: "#FFFFFF";
        readonly success: "#22C55E";
        readonly successForeground: "#FFFFFF";
        readonly warning: "#F59E0B";
        readonly warningForeground: "#FFFFFF";
        readonly accent: "#33D6C9";
        readonly accentForeground: "#1A1A1A";
    };
    readonly 'team-relay': {
        readonly primary: "#6366F1";
        readonly primaryForeground: "#FFFFFF";
        readonly secondary: "#E0E7FF";
        readonly secondaryForeground: "#3730A3";
        readonly background: "#FFFFFF";
        readonly foreground: "#1E1B4B";
        readonly muted: "#F5F3FF";
        readonly mutedForeground: "#6B7280";
        readonly card: "#FFFFFF";
        readonly cardForeground: "#1E1B4B";
        readonly border: "#E5E7EB";
        readonly input: "#E5E7EB";
        readonly ring: "#6366F1";
        readonly destructive: "#EF4444";
        readonly destructiveForeground: "#FFFFFF";
        readonly success: "#10B981";
        readonly successForeground: "#FFFFFF";
        readonly warning: "#F59E0B";
        readonly warningForeground: "#FFFFFF";
        readonly accent: "#8B5CF6";
        readonly accentForeground: "#FFFFFF";
    };
};
type ThemeName = keyof typeof themes;

/**
 * Entire VC Typography System
 */
declare const fontFamily: {
    readonly sans: readonly ["Inter", "system-ui", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"];
    readonly mono: readonly ["JetBrains Mono", "Fira Code", "Monaco", "Consolas", "monospace"];
};
declare const fontSize: {
    readonly xs: readonly ["0.75rem", {
        readonly lineHeight: "1rem";
    }];
    readonly sm: readonly ["0.875rem", {
        readonly lineHeight: "1.25rem";
    }];
    readonly base: readonly ["1rem", {
        readonly lineHeight: "1.5rem";
    }];
    readonly lg: readonly ["1.125rem", {
        readonly lineHeight: "1.75rem";
    }];
    readonly xl: readonly ["1.25rem", {
        readonly lineHeight: "1.75rem";
    }];
    readonly '2xl': readonly ["1.5rem", {
        readonly lineHeight: "2rem";
    }];
    readonly '3xl': readonly ["1.875rem", {
        readonly lineHeight: "2.25rem";
    }];
    readonly '4xl': readonly ["2.25rem", {
        readonly lineHeight: "2.5rem";
    }];
    readonly '5xl': readonly ["3rem", {
        readonly lineHeight: "1";
    }];
    readonly '6xl': readonly ["3.75rem", {
        readonly lineHeight: "1";
    }];
};
declare const fontWeight: {
    readonly normal: "400";
    readonly medium: "500";
    readonly semibold: "600";
    readonly bold: "700";
};
declare const letterSpacing: {
    readonly tighter: "-0.05em";
    readonly tight: "-0.025em";
    readonly normal: "0em";
    readonly wide: "0.025em";
    readonly wider: "0.05em";
};

/**
 * Entire VC Spacing System
 * Based on 4px grid
 */
declare const spacing: {
    readonly px: "1px";
    readonly 0: "0";
    readonly 0.5: "0.125rem";
    readonly 1: "0.25rem";
    readonly 1.5: "0.375rem";
    readonly 2: "0.5rem";
    readonly 2.5: "0.625rem";
    readonly 3: "0.75rem";
    readonly 3.5: "0.875rem";
    readonly 4: "1rem";
    readonly 5: "1.25rem";
    readonly 6: "1.5rem";
    readonly 7: "1.75rem";
    readonly 8: "2rem";
    readonly 9: "2.25rem";
    readonly 10: "2.5rem";
    readonly 11: "2.75rem";
    readonly 12: "3rem";
    readonly 14: "3.5rem";
    readonly 16: "4rem";
    readonly 20: "5rem";
    readonly 24: "6rem";
    readonly 28: "7rem";
    readonly 32: "8rem";
    readonly 36: "9rem";
    readonly 40: "10rem";
    readonly 44: "11rem";
    readonly 48: "12rem";
    readonly 52: "13rem";
    readonly 56: "14rem";
    readonly 60: "15rem";
    readonly 64: "16rem";
    readonly 72: "18rem";
    readonly 80: "20rem";
    readonly 96: "24rem";
};
declare const borderRadius: {
    readonly none: "0";
    readonly sm: "0.125rem";
    readonly DEFAULT: "0.375rem";
    readonly md: "0.5rem";
    readonly lg: "0.75rem";
    readonly xl: "1rem";
    readonly '2xl': "1.5rem";
    readonly '3xl': "2rem";
    readonly full: "9999px";
};
declare const borderWidth: {
    readonly DEFAULT: "1px";
    readonly 0: "0";
    readonly 2: "2px";
    readonly 4: "4px";
    readonly 8: "8px";
};

/**
 * Entire VC Shadows & Effects
 */
declare const boxShadow: {
    readonly sm: "0 1px 2px 0 rgb(0 0 0 / 0.05)";
    readonly DEFAULT: "0 1px 3px 0 rgb(0 0 0 / 0.1), 0 1px 2px -1px rgb(0 0 0 / 0.1)";
    readonly md: "0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)";
    readonly lg: "0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)";
    readonly xl: "0 20px 25px -5px rgb(0 0 0 / 0.1), 0 8px 10px -6px rgb(0 0 0 / 0.1)";
    readonly '2xl': "0 25px 50px -12px rgb(0 0 0 / 0.25)";
    readonly inner: "inset 0 2px 4px 0 rgb(0 0 0 / 0.05)";
    readonly none: "none";
};
declare const animation: {
    readonly none: "none";
    readonly spin: "spin 1s linear infinite";
    readonly ping: "ping 1s cubic-bezier(0, 0, 0.2, 1) infinite";
    readonly pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite";
    readonly bounce: "bounce 1s infinite";
    readonly 'accordion-down': "accordion-down 0.2s ease-out";
    readonly 'accordion-up': "accordion-up 0.2s ease-out";
    readonly 'fade-in': "fade-in 0.2s ease-out";
    readonly 'fade-out': "fade-out 0.2s ease-out";
    readonly 'slide-in-from-top': "slide-in-from-top 0.2s ease-out";
    readonly 'slide-in-from-bottom': "slide-in-from-bottom 0.2s ease-out";
    readonly 'slide-in-from-left': "slide-in-from-left 0.2s ease-out";
    readonly 'slide-in-from-right': "slide-in-from-right 0.2s ease-out";
    readonly 'caret-blink': "caret-blink 1.25s ease-out infinite";
};
declare const keyframes: {
    readonly 'accordion-down': {
        readonly from: {
            readonly height: "0";
        };
        readonly to: {
            readonly height: "var(--radix-accordion-content-height)";
        };
    };
    readonly 'accordion-up': {
        readonly from: {
            readonly height: "var(--radix-accordion-content-height)";
        };
        readonly to: {
            readonly height: "0";
        };
    };
    readonly 'fade-in': {
        readonly from: {
            readonly opacity: "0";
        };
        readonly to: {
            readonly opacity: "1";
        };
    };
    readonly 'fade-out': {
        readonly from: {
            readonly opacity: "1";
        };
        readonly to: {
            readonly opacity: "0";
        };
    };
    readonly 'slide-in-from-top': {
        readonly from: {
            readonly transform: "translateY(-100%)";
        };
        readonly to: {
            readonly transform: "translateY(0)";
        };
    };
    readonly 'slide-in-from-bottom': {
        readonly from: {
            readonly transform: "translateY(100%)";
        };
        readonly to: {
            readonly transform: "translateY(0)";
        };
    };
    readonly 'slide-in-from-left': {
        readonly from: {
            readonly transform: "translateX(-100%)";
        };
        readonly to: {
            readonly transform: "translateX(0)";
        };
    };
    readonly 'slide-in-from-right': {
        readonly from: {
            readonly transform: "translateX(100%)";
        };
        readonly to: {
            readonly transform: "translateX(0)";
        };
    };
    readonly 'caret-blink': {
        readonly '0%,70%,100%': {
            readonly opacity: "1";
        };
        readonly '20%,50%': {
            readonly opacity: "0";
        };
    };
};
declare const transitionDuration: {
    readonly 75: "75ms";
    readonly 100: "100ms";
    readonly 150: "150ms";
    readonly 200: "200ms";
    readonly 300: "300ms";
    readonly 500: "500ms";
    readonly 700: "700ms";
    readonly 1000: "1000ms";
};
declare const transitionTimingFunction: {
    readonly DEFAULT: "cubic-bezier(0.4, 0, 0.2, 1)";
    readonly linear: "linear";
    readonly in: "cubic-bezier(0.4, 0, 1, 1)";
    readonly out: "cubic-bezier(0, 0, 0.2, 1)";
    readonly 'in-out': "cubic-bezier(0.4, 0, 0.2, 1)";
};

export { type ThemeColors, type ThemeName, animation, borderRadius, borderWidth, boxShadow, entireColors, fontFamily, fontSize, fontWeight, hexToRgb, hexToRgbString, keyframes, letterSpacing, playgroundColors, spacing, sparkColors, teamRelayColors, themes, transitionDuration, transitionTimingFunction };
