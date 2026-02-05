<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLHeadingElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<h5 class={cn('mb-1 font-medium leading-none tracking-tight', className)} {...restProps}>
  {@render children?.()}
</h5>
