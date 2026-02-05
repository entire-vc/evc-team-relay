<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLDivElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<div class={cn('rounded-xl border bg-card text-card-foreground shadow', className)} {...restProps}>
  {@render children?.()}
</div>
