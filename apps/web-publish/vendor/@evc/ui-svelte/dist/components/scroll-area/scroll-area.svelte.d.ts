import type { HTMLAttributes } from 'svelte/elements';
type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    orientation?: 'vertical' | 'horizontal' | 'both';
};
declare const ScrollArea: import("svelte").Component<Props, {}, "">;
type ScrollArea = ReturnType<typeof ScrollArea>;
export default ScrollArea;
//# sourceMappingURL=scroll-area.svelte.d.ts.map