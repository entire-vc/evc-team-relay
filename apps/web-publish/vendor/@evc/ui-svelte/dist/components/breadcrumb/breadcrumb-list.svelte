<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLOListElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<ol
  class={cn(
    'flex flex-wrap items-center gap-1.5 break-words text-sm text-muted-foreground sm:gap-2.5',
    className
  )}
  {...restProps}
>
  {@render children?.()}
</ol>
