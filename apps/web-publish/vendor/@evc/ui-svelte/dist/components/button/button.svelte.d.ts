import { type ButtonVariants } from './variants.js';
import type { HTMLButtonAttributes } from 'svelte/elements';
type Props = HTMLButtonAttributes & ButtonVariants & {
    class?: string;
};
declare const Button: import("svelte").Component<Props, {}, "">;
type Button = ReturnType<typeof Button>;
export default Button;
//# sourceMappingURL=button.svelte.d.ts.map