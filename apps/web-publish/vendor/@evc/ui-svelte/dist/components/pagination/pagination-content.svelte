<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLUListElement> & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<ul class={cn('flex flex-row items-center gap-1', className)} {...restProps}>
  {@render children?.()}
</ul>
