import type { HTMLAttributes } from 'svelte/elements';
type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    orientation?: 'horizontal' | 'vertical';
    decorative?: boolean;
};
declare const Separator: import("svelte").Component<Props, {}, "">;
type Separator = ReturnType<typeof Separator>;
export default Separator;
//# sourceMappingURL=separator.svelte.d.ts.map