<script lang="ts">
  import { ContextMenu as ContextMenuPrimitive } from 'bits-ui';
  import { cn } from '../../utils.js';

  type Props = ContextMenuPrimitive.ItemProps & {
    class?: string;
    inset?: boolean;
  };

  let { class: className, inset = false, children, ...restProps }: Props = $props();
</script>

<ContextMenuPrimitive.Item
  class={cn(
    'relative flex cursor-default select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none focus:bg-accent focus:text-accent-foreground data-[disabled]:pointer-events-none data-[disabled]:opacity-50',
    inset && 'pl-8',
    className
  )}
  {...restProps}
>
  {@render children?.()}
</ContextMenuPrimitive.Item>
