<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLSpanElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<span class={cn('ml-auto text-xs tracking-widest opacity-60', className)} {...restProps}>
  {@render children?.()}
</span>
