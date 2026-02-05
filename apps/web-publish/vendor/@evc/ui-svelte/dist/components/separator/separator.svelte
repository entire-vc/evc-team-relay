<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    orientation?: 'horizontal' | 'vertical';
    decorative?: boolean;
  };

  let {
    class: className,
    orientation = 'horizontal',
    decorative = true,
    ...restProps
  }: Props = $props();
</script>

<div
  role={decorative ? 'none' : 'separator'}
  aria-orientation={decorative ? undefined : orientation}
  class={cn(
    'shrink-0 bg-border',
    orientation === 'horizontal' ? 'h-[1px] w-full' : 'h-full w-[1px]',
    className
  )}
  {...restProps}
></div>
