import { type ButtonSize } from '../button/variants.js';
import type { HTMLAnchorAttributes } from 'svelte/elements';
type Props = HTMLAnchorAttributes & {
    class?: string;
    isActive?: boolean;
    size?: ButtonSize;
};
declare const PaginationLink: import("svelte").Component<Props, {}, "">;
type PaginationLink = ReturnType<typeof PaginationLink>;
export default PaginationLink;
//# sourceMappingURL=pagination-link.svelte.d.ts.map