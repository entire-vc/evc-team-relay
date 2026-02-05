<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    value?: number;
    max?: number;
  };

  let { class: className, value = 0, max = 100, ...restProps }: Props = $props();

  const percentage = $derived(Math.min(Math.max((value / max) * 100, 0), 100));
</script>

<div
  role="progressbar"
  aria-valuemin={0}
  aria-valuemax={max}
  aria-valuenow={value}
  class={cn('relative h-2 w-full overflow-hidden rounded-full bg-primary/20', className)}
  {...restProps}
>
  <div
    class="h-full w-full flex-1 bg-primary transition-all"
    style:transform={`translateX(-${100 - percentage}%)`}
  ></div>
</div>
