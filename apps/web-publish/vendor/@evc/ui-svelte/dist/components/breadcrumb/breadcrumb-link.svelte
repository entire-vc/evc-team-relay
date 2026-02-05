<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAnchorAttributes } from 'svelte/elements';

  type Props = HTMLAnchorAttributes & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<a class={cn('transition-colors hover:text-foreground', className)} {...restProps}>
  {@render children?.()}
</a>
