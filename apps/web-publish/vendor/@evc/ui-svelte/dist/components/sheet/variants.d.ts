import { type VariantProps } from 'class-variance-authority';
export declare const sheetVariants: (props?: ({
    side?: "top" | "bottom" | "left" | "right" | null | undefined;
} & import("class-variance-authority/types").ClassProp) | undefined) => string;
export type SheetSide = VariantProps<typeof sheetVariants>['side'];
export type SheetVariants = VariantProps<typeof sheetVariants>;
//# sourceMappingURL=variants.d.ts.map