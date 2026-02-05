<script lang="ts">
  import { cn } from '../../utils.js';
  import { buttonVariants, type ButtonVariants } from './variants.js';
  import type { HTMLButtonAttributes } from 'svelte/elements';

  type Props = HTMLButtonAttributes &
    ButtonVariants & {
      class?: string;
    };

  let {
    class: className,
    variant = 'default',
    size = 'default',
    type = 'button',
    children,
    ...restProps
  }: Props = $props();
</script>

<button
  type={type}
  class={cn(buttonVariants({ variant, size }), className)}
  {...restProps}
>
  {@render children?.()}
</button>
