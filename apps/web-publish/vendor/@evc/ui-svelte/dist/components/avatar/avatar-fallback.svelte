<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLSpanElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<span
  class={cn('flex h-full w-full items-center justify-center rounded-full bg-muted', className)}
  {...restProps}
>
  {@render children?.()}
</span>
