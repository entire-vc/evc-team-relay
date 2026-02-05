<script lang="ts">
  import { cn } from '../../utils.js';
  import type { HTMLAttributes } from 'svelte/elements';

  type Props = HTMLAttributes<HTMLSpanElement> & {
    class?: string;
    placeholder?: string;
  };

  let { class: className, placeholder, children, ...restProps }: Props = $props();
</script>

<span class={cn(className)} {...restProps}>
  {#if children}
    {@render children()}
  {:else}
    {placeholder}
  {/if}
</span>
