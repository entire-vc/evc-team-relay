<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    orientation?: 'vertical' | 'horizontal';
  };

  let { class: className, orientation = 'vertical', ...restProps }: Props = $props();
</script>

<div
  class={cn(
    'flex touch-none select-none transition-colors',
    orientation === 'vertical' && 'h-full w-2.5 border-l border-l-transparent p-[1px]',
    orientation === 'horizontal' && 'h-2.5 flex-col border-t border-t-transparent p-[1px]',
    className
  )}
  {...restProps}
>
  <div class="relative flex-1 rounded-full bg-border"></div>
</div>
