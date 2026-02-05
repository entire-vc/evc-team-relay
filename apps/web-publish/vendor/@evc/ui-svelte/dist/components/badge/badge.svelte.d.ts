import { type BadgeVariants } from './variants.js';
import type { HTMLAttributes } from 'svelte/elements';
type Props = HTMLAttributes<HTMLDivElement> & BadgeVariants & {
    class?: string;
};
declare const Badge: import("svelte").Component<Props, {}, "">;
type Badge = ReturnType<typeof Badge>;
export default Badge;
//# sourceMappingURL=badge.svelte.d.ts.map