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

<li role="presentation" aria-hidden="true" class={cn('[&>svg]:h-3.5 [&>svg]:w-3.5', className)} {...restProps}>
  {#if children}
    {@render children()}
  {:else}
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    >
      <path d="m9 18 6-6-6-6" />
    </svg>
  {/if}
</li>
