import { type AlertVariants } from './variants.js';
import type { HTMLAttributes } from 'svelte/elements';
type Props = HTMLAttributes<HTMLDivElement> & AlertVariants & {
    class?: string;
};
declare const Alert: import("svelte").Component<Props, {}, "">;
type Alert = ReturnType<typeof Alert>;
export default Alert;
//# sourceMappingURL=alert.svelte.d.ts.map