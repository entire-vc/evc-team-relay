<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLHeadingElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<h3 class={cn('font-semibold leading-none tracking-tight', className)} {...restProps}>
  {@render children?.()}
</h3>
