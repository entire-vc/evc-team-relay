<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLTextareaAttributes } from 'svelte/elements';

  type Props = HTMLTextareaAttributes & {
    class?: string;
  };

  let { class: className, ...restProps }: Props = $props();
</script>

<textarea
  class={cn(
    'flex min-h-[60px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50',
    className
  )}
  {...restProps}
></textarea>
