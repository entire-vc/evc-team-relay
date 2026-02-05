import type { HTMLLiAttributes } from 'svelte/elements';
import type { Snippet } from 'svelte';
type Props = HTMLLiAttributes & {
    class?: string;
    children?: Snippet;
};
declare const BreadcrumbSeparator: import("svelte").Component<Props, {}, "">;
type BreadcrumbSeparator = ReturnType<typeof BreadcrumbSeparator>;
export default BreadcrumbSeparator;
//# sourceMappingURL=breadcrumb-separator.svelte.d.ts.map