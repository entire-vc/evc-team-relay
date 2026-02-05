<script lang="ts">
  import { Select as SelectPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';
  import type { Snippet } from 'svelte';

  type Props = SelectPrimitive.ItemProps & {
    class?: string;
    label?: string;
  };

  let { class: className, label, ...restProps }: Props = $props();
</script>

<SelectPrimitive.Item
  class={cn(
    'relative flex w-full cursor-default select-none items-center rounded-sm py-1.5 pl-2 pr-8 text-sm outline-none data-[disabled]:pointer-events-none data-[highlighted]:bg-accent data-[highlighted]:text-accent-foreground data-[disabled]:opacity-50',
    className
  )}
  {label}
  {...restProps}
>
  {#snippet children({ selected })}
    <span class="absolute right-2 flex h-3.5 w-3.5 items-center justify-center">
      {#if selected}
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
          class="h-4 w-4"
        >
          <path d="M20 6 9 17l-5-5" />
        </svg>
      {/if}
    </span>
    {label}
  {/snippet}
</SelectPrimitive.Item>
