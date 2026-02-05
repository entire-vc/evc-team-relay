<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLLiAttributes } from 'svelte/elements';
  import type { Snippet } from 'svelte';

  type Props = HTMLLiAttributes & {
    class?: string;
    children?: Snippet;
  };

  let { class: className, children, ...restProps }: Props = $props();
</script>

<li
  data-sidebar="menu-item"
  class={cn('group/menu-item relative', className)}
  {...restProps}
>
  {@render children?.()}
</li>
