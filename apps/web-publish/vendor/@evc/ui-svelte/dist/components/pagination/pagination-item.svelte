<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLLiAttributes } from 'svelte/elements';

  type Props = HTMLLiAttributes & {
    class?: string;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<li class={cn('', className)} {...restProps}>
  {@render children?.()}
</li>
