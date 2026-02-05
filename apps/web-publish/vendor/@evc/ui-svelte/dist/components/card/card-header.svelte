<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<div class={cn('flex flex-col space-y-1.5 p-6', className)} {...restProps}>
  {@render children?.()}
</div>
