<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
    ratio?: number;
  };

  let { class: className, ratio = 1, children, ...restProps }: Props = $props();
</script>

<div
  class={cn('relative w-full', className)}
  style:padding-bottom={`${100 / ratio}%`}
  {...restProps}
>
  <div class="absolute inset-0">
    {@render children?.()}
  </div>
</div>
