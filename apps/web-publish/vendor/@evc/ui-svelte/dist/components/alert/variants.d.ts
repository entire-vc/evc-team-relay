import { type VariantProps } from 'class-variance-authority';
export declare const alertVariants: (props?: ({
    variant?: "default" | "destructive" | "success" | "warning" | null | undefined;
} & import("class-variance-authority/types").ClassProp) | undefined) => string;
export type AlertVariant = VariantProps<typeof alertVariants>['variant'];
export type AlertVariants = VariantProps<typeof alertVariants>;
//# sourceMappingURL=variants.d.ts.map