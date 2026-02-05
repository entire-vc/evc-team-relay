<script lang="ts">
  import { cn } from '../../utils.js';
  import { buttonVariants, type ButtonSize } from '../button/variants.js';
  import type { HTMLAnchorAttributes } from 'svelte/elements';

  type Props = HTMLAnchorAttributes & {
    class?: string;
    isActive?: boolean;
    size?: ButtonSize;
  };

  let { class: className, isActive = false, size = 'icon', children, ...restProps }: Props = $props();
</script>

<a
  aria-current={isActive ? 'page' : undefined}
  class={cn(
    buttonVariants({
      variant: isActive ? 'outline' : 'ghost',
      size
    }),
    className
  )}
  {...restProps}
>
  {@render children?.()}
</a>
