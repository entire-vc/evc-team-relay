import { type VariantProps } from 'class-variance-authority';
export declare const badgeVariants: (props?: ({
    variant?: "default" | "destructive" | "outline" | "secondary" | "success" | "warning" | null | undefined;
} & import("class-variance-authority/types").ClassProp) | undefined) => string;
export type BadgeVariant = VariantProps<typeof badgeVariants>['variant'];
export type BadgeVariants = VariantProps<typeof badgeVariants>;
//# sourceMappingURL=variants.d.ts.map