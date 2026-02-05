<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    inset?: boolean;
  };

  let { class: className, inset = false, children, ...restProps }: Props = $props();
</script>

<div
  class={cn('px-2 py-1.5 text-sm font-semibold', inset && 'pl-8', className)}
  {...restProps}
>
  {@render children?.()}
</div>
