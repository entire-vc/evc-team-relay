import { type VariantProps } from 'class-variance-authority';
export declare const toggleVariants: (props?: ({
    variant?: "default" | "outline" | null | undefined;
    size?: "default" | "sm" | "lg" | null | undefined;
} & import("class-variance-authority/types").ClassProp) | undefined) => string;
export type ToggleVariant = VariantProps<typeof toggleVariants>['variant'];
export type ToggleSize = VariantProps<typeof toggleVariants>['size'];
export type ToggleVariants = VariantProps<typeof toggleVariants>;
//# sourceMappingURL=variants.d.ts.map